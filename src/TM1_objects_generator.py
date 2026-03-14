import json
import logging
from typing import Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


class TM1Creator:

    def __init__(
        self,
        json_file: str,
        simulate: bool = True,
        tm1_config: Optional[dict] = None
    ):
        """
        Initialize TM1 Creator

        Args:
            json_file: Path to JSON specification file
            simulate: If True, only simulate creation
            tm1_config: TM1 connection configuration
                {
                    'address': 'localhost',
                    'port': 8001,
                    'user': 'admin',
                    'password': 'apple',
                    'ssl': False
                }
        """
        self.json_file = json_file
        self.simulate = simulate
        self.tm1_config = tm1_config
        self.spec = None
        self.tm1 = None

        # Statistics
        self.stats = {
            'dimensions_created': 0,
            'dimensions_skipped': 0,
            'dimensions_failed': 0,
            'cubes_created': 0,
            'cubes_skipped': 0,
            'cubes_failed': 0
        }

    # ==========================================
    # SETUP METHODS
    # ==========================================

    def load_json(self):
        """Load JSON specification file"""
        try:
            with open(self.json_file) as f:
                self.spec = json.load(f)
            logger.info(
                f"Loaded spec: {self.spec['model']['name']}"
            )
            return self
        except FileNotFoundError:
            logger.error(f"File not found: {self.json_file}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON: {e}")
            raise

    def connect(self):
        """Connect to TM1 server"""
        if self.simulate:
            logger.info("Running in SIMULATION mode")
            return self

        if not self.tm1_config:
            raise ValueError(
                "TM1 config required for real mode!"
            )

        try:
            from TM1py import TM1Service
            self.tm1 = TM1Service(**self.tm1_config)
            logger.info(
                f"Connected to TM1: "
                f"{self.tm1_config['address']}:"
                f"{self.tm1_config['port']}"
            )
        except ImportError:
            logger.error(
                "TM1py not installed! "
                "Run: pip install TM1py"
            )
            raise
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            raise

        return self

    def disconnect(self):
        """Disconnect from TM1 server"""
        if self.tm1:
            self.tm1.logout()
            logger.info("Disconnected from TM1")

    # ==========================================
    # DIMENSION METHODS
    # ==========================================

    def _dimension_exists(self, name: str) -> bool:
        """Check if dimension exists"""
        if self.simulate:
            return False
        return self.tm1.dimensions.exists(name)

    def _create_dimension_object(self, dim_spec: dict):
        """
        Create TM1py dimension object from spec

        Args:
            dim_spec: Dimension specification dict
        """
        from TM1py.Objects import (
            Dimension,
            Hierarchy,
            Element,
            ElementAttribute
        )

        dimension = Dimension(dim_spec['name'])
        hierarchy = Hierarchy(
            dim_spec['name'],
            dim_spec['name']
        )

        # Process hierarchies
        for hier_spec in dim_spec.get('hierarchies', []):

            # Add elements
            for elem_spec in hier_spec.get('elements', []):
                elem_name = elem_spec['name']
                elem_type = elem_spec['type']

                # Map types to TM1py element types
                if elem_type == 'Consolidated':
                    tm1_type = 'Consolidated'
                elif elem_type == 'String':
                    tm1_type = 'String'
                else:
                    tm1_type = 'Numeric'

                element = Element(elem_name, tm1_type)
                hierarchy.add_element(element)

            # Add edges
            for edge_spec in hier_spec.get('edges', []):
                hierarchy.add_edge(
                    edge_spec['parent'],
                    edge_spec['child'],
                    edge_spec['weight']
                )

        dimension.add_hierarchy(hierarchy)
        return dimension

    def _process_dimension(self, dim_spec: dict):
        """
        Process single dimension creation

        Args:
            dim_spec: Dimension specification dict
        """
        dim_name = dim_spec['name']

        # Skip if no hierarchies defined
        # (will be created with cube)
        if not dim_spec.get('hierarchies') or \
           not any(
               h.get('elements')
               for h in dim_spec.get('hierarchies', [])
           ):
            logger.info(
                f"  Skipping empty dimension: {dim_name}"
            )
            return

        logger.info(f"  Processing dimension: {dim_name}")

        # Count elements for logging
        elem_count = sum(
            len(h.get('elements', []))
            for h in dim_spec.get('hierarchies', [])
        )
        edge_count = sum(
            len(h.get('edges', []))
            for h in dim_spec.get('hierarchies', [])
        )
        cons_count = sum(
            1
            for h in dim_spec.get('hierarchies', [])
            for e in h.get('elements', [])
            if e['type'] == 'Consolidated'
        )

        logger.info(
            f"    Elements: {elem_count} "
            f"({cons_count} consolidated), "
            f"Edges: {edge_count}"
        )

        # Check for source references
        for hier_spec in dim_spec.get('hierarchies', []):
            for elem in hier_spec.get('elements', []):
                if 'source' in elem:
                    logger.info(
                        f"    Source reference: "
                        f"{elem['name']} -> "
                        f"{elem['source']['cube']}."
                        f"{elem['source']['element']}"
                    )

        if self.simulate:
            logger.info(
                f"    [SIMULATE] Would create: {dim_name}"
            )
            self.stats['dimensions_created'] += 1
            return

        try:
            if self._dimension_exists(dim_name):
                logger.warning(
                    f"    Dimension exists: {dim_name} "
                    f"- Skipping"
                )
                self.stats['dimensions_skipped'] += 1
                return

            dimension = self._create_dimension_object(
                dim_spec
            )
            self.tm1.dimensions.create(dimension)
            logger.info(
                f"    Created dimension: {dim_name}"
            )
            self.stats['dimensions_created'] += 1

        except Exception as e:
            logger.error(
                f"    Failed to create dimension "
                f"{dim_name}: {e}"
            )
            self.stats['dimensions_failed'] += 1

    # ==========================================
    # CUBE METHODS
    # ==========================================

    def _cube_exists(self, name: str) -> bool:
        """Check if cube exists"""
        if self.simulate:
            return False
        return self.tm1.cubes.exists(name)

    def _get_cube_dimensions(
        self,
        cube_spec: dict
    ) -> list:
        """
        Get ordered list of dimension names for cube

        Order: specific dimensions first,
               then shared dimensions
        """
        dim_names = []

        # Add specific dimensions
        for dim in cube_spec['specific_dimensions']:
            if dim['name'] not in dim_names:
                dim_names.append(dim['name'])

        # Add shared dimensions
        for shared in cube_spec['shared_dimensions']:
            if shared not in dim_names:
                dim_names.append(shared)

        return dim_names

    def _process_cube(self, cube_spec: dict):
        """
        Process single cube creation

        Args:
            cube_spec: Cube specification dict
        """
        cube_name = cube_spec['name']
        logger.info(f"  Processing cube: {cube_name}")

        # Get dimension list
        dim_names = self._get_cube_dimensions(cube_spec)
        logger.info(
            f"    Dimensions ({len(dim_names)}): "
            f"{', '.join(dim_names)}"
        )

        # Log related cubes
        for related in cube_spec.get('related_cubes', []):
            logger.info(
                f"    Related cube: {related['name']} "
                f"({related['relationship']})"
            )

        if self.simulate:
            logger.info(
                f"    [SIMULATE] Would create: {cube_name}"
            )
            self.stats['cubes_created'] += 1
            return

        try:
            if self._cube_exists(cube_name):
                logger.warning(
                    f"    Cube exists: {cube_name} "
                    f"- Skipping"
                )
                self.stats['cubes_skipped'] += 1
                return

            from TM1py.Objects import Cube
            cube = Cube(cube_name, dim_names)
            self.tm1.cubes.create(cube)
            logger.info(f"    Created cube: {cube_name}")
            self.stats['cubes_created'] += 1

        except Exception as e:
            logger.error(
                f"    Failed to create cube "
                f"{cube_name}: {e}"
            )
            self.stats['cubes_failed'] += 1

    # ==========================================
    # VALIDATION
    # ==========================================

    def validate(self) -> bool:
        """
        Validate JSON specification before creation
        """
        logger.info("Validating specification...")
        errors = []
        warnings = []

        # Collect all dimension names
        all_dims = [
            d['name']
            for d in self.spec['shared_dimensions']
        ]
        for cube in self.spec['cubes']:
            for dim in cube['specific_dimensions']:
                all_dims.append(dim['name'])

        # Validate cubes
        for cube in self.spec['cubes']:

            # Check cube has dimensions
            if not cube['specific_dimensions'] and \
               not cube['shared_dimensions']:
                errors.append(
                    f"Cube '{cube['name']}' has no dimensions!"
                )

            # Check shared dimension references
            for shared in cube['shared_dimensions']:
                if shared not in all_dims:
                    errors.append(
                        f"Cube '{cube['name']}' references "
                        f"unknown dimension '{shared}'"
                    )

            # Check specific dimensions
            for dim in cube['specific_dimensions']:

                # Check for spaces in names
                if ' ' in dim['name']:
                    warnings.append(
                        f"Dimension name has spaces: "
                        f"'{dim['name']}'"
                    )

                # Check element source references
                for hier in dim.get('hierarchies', []):
                    for elem in hier.get('elements', []):

                        # Validate source references
                        if 'source' in elem:
                            src = elem['source']
                            src_cube = src.get('cube')
                            cube_names = [
                                c['name']
                                for c in self.spec['cubes']
                            ]
                            if src_cube and \
                               src_cube not in cube_names:
                                errors.append(
                                    f"Element "
                                    f"'{elem['name']}' "
                                    f"references unknown "
                                    f"cube '{src_cube}'"
                                )

                        # Validate Time references
                        if 'references' in elem:
                            ref = elem['references']
                            if ref not in all_dims:
                                errors.append(
                                    f"Element "
                                    f"'{elem['name']}' "
                                    f"references unknown "
                                    f"dimension '{ref}'"
                                )

                    # Validate edges
                    elem_names = [
                        e['name']
                        for e in hier.get('elements', [])
                    ]
                    for edge in hier.get('edges', []):
                        if edge['parent'] not in elem_names:
                            errors.append(
                                f"Edge parent "
                                f"'{edge['parent']}' "
                                f"not found in "
                                f"'{dim['name']}'"
                            )
                        if edge['child'] not in elem_names:
                            errors.append(
                                f"Edge child "
                                f"'{edge['child']}' "
                                f"not found in "
                                f"'{dim['name']}'"
                            )

        # Report results
        if warnings:
            for w in warnings:
                logger.warning(w)

        if errors:
            for e in errors:
                logger.error(e)
            logger.error(
                f"Validation failed: "
                f"{len(errors)} errors found!"
            )
            return False

        logger.info("Validation passed!")
        return True

    # ==========================================
    # MAIN PROCESS
    # ==========================================

    def run(self):
        """Main process to create TM1 objects"""

        # Load JSON
        self.load_json()

        # Validate
        if not self.validate():
            raise ValueError(
                "Validation failed! Fix errors first."
            )

        # Connect
        self.connect()

        try:
            mode = "SIMULATION" if self.simulate \
                else "REAL"
            logger.info(
                f"\nStarting TM1 object creation "
                f"[{mode} MODE]"
            )
            logger.info(
                f"Model: {self.spec['model']['name']} "
                f"v{self.spec['model']['version']}"
            )

            # Step 1: Create shared dimensions
            logger.info("\n--- Shared Dimensions ---")
            for dim_spec in self.spec['shared_dimensions']:
                self._process_dimension(dim_spec)

            # Step 2: Process cubes
            logger.info("\n--- Cubes ---")
            for cube_spec in self.spec['cubes']:
                logger.info(
                    f"\nProcessing: {cube_spec['name']}"
                )

                # Create specific dimensions first
                logger.info("  Specific dimensions:")
                for dim_spec in \
                        cube_spec['specific_dimensions']:
                    self._process_dimension(dim_spec)

                # Create cube
                logger.info("  Creating cube:")
                self._process_cube(cube_spec)

        finally:
            self.disconnect()

        # Print summary
        self.print_summary()

    # ==========================================
    # SUMMARY
    # ==========================================

    def print_summary(self):
        """Print creation summary"""
        print("\n" + "="*50)
        print(" CREATION SUMMARY")
        print("="*50)
        print(
            f"Mode: "
            f"{'SIMULATION' if self.simulate else 'REAL'}"
        )
        print(f"\nModel: {self.spec['model']['name']}")
        print(f"Version: {self.spec['model']['version']}")
        print("\nDimensions:")
        print(
            f"  Created : "
            f"{self.stats['dimensions_created']}"
        )
        print(
            f"  Skipped : "
            f"{self.stats['dimensions_skipped']}"
        )
        print(
            f"  Failed  : "
            f"{self.stats['dimensions_failed']}"
        )
        print("\nCubes:")
        print(
            f"  Created : "
            f"{self.stats['cubes_created']}"
        )
        print(
            f"  Skipped : "
            f"{self.stats['cubes_skipped']}"
        )
        print(
            f"  Failed  : "
            f"{self.stats['cubes_failed']}"
        )
        print("="*50 + "\n")


# ==========================================
# RUN
# ==========================================

def main():

    # Simulation mode (no TM1 server needed)
    creator = TM1Creator(
        json_file='tm1_spec.json',
        simulate=True
    )
    creator.run()

    # ----------------------------------------
    # Real mode (uncomment when server available)
    # ----------------------------------------
    # creator = TM1Creator(
    #     json_file='tm1_spec.json',
    #     simulate=False,
    #     tm1_config={
    #         'address': 'localhost',
    #         'port': 8001,
    #         'user': 'admin',
    #         'password': 'apple',
    #         'ssl': False
    #     }
    # )
    # creator.run()


if __name__ == "__main__":
    main()