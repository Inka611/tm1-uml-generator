import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


class PlantUMLParser:

    def __init__(self):
        self.cubes = {}
        self.dimensions = {}
        self.relationships = []
        self.notes = []
        self.current_class = None
        self.current_type = None
        self.current_note = None
        self.in_note = False

        # Regex patterns
        self.patterns = {
            # class CAPEX <<Cube>> {
            'cube': re.compile(
                r'class\s+(\w+)\s+<<Cube>>'
            ),
            # class Department <<Dimension>> {
            'dimension': re.compile(
                r'class\s+(\w+)\s+<<Dimension>>'
            ),
            # +type: Measures
            'type': re.compile(
                r'\+type:\s*(\w+)'
            ),
            # +Description: some text
            'description': re.compile(
                r'\+Description:\s*(.+)'
            ),
            # +Amount: Numeric  or  +Year: String
            'element_typed': re.compile(
                r'\+(\w+):\s*(Numeric|String|Consolidated)'
            ),
            # +elements: Amount(N), Year(S)
            'elements': re.compile(
                r'\+elements:\s*(.+)'
            ),
            # --Leaf Elements-- or --Consolidated Elements--
            'separator': re.compile(
                r'^--(.+)--$'
            ),
            # CAPEX *-- CAPEX_prop : label
            'composition': re.compile(
                r'(\w+)\s+\*--\s+(\w+)(?:\s*:\s*(.+))?'
            ),
            # CAPEX o-- Department : label
            'aggregation': re.compile(
                r'(\w+)\s+o--\s+(\w+)(?:\s*:\s*(.+))?'
            ),
            # Capex_prop_m ..> Time_by_Month : label
            'dependency': re.compile(
                r'(\w+)\s+\.\.>\s+(\w+)(?:\s*:\s*(.+))?'
            ),
            # note right of ClassName
            'note_start': re.compile(
                r'note\s+\w+\s+of\s+(\w+)'
            ),
            # end note
            'note_end': re.compile(
                r'^end\s+note$'
            ),
            # + Total_Amount (weight: +1)
            'consolidation_rule': re.compile(
                r'[+\-]\s*(\w+)\s+\(weight:\s*([+\-]\d+)\)'
            ),
            # source_cube: CAPEX
            'source_cube': re.compile(
                r'[Cc]ube:\s*(\w+)'
            ),
            # source_element: Accumulated_Depreciation
            'source_element': re.compile(
                r'[Ee]lement:\s*(\w+)'
            ),
            # source_dimension: Capex_m
            'source_dimension': re.compile(
                r'[Dd]imension:\s*(\w+)'
            )
        }

    # ==========================================
    # UTILITY METHODS
    # ==========================================

    def sanitize_name(self, name):
        """No spaces allowed in TM1 names!"""
        name = name.strip()
        name = name.replace(' ', '_')
        name = re.sub(r'[^a-zA-Z0-9_]', '', name)
        return name

    def parse_element_type(self, type_str):
        """Convert type string to standard format"""
        type_map = {
            'Numeric': 'Numeric',
            'String': 'String',
            'Consolidated': 'Consolidated',
            'N': 'Numeric',
            'S': 'String',
            'C': 'Consolidated'
        }
        return type_map.get(type_str, 'Numeric')

    def parse_inline_elements(self, elements_str):
        """
        Parse inline elements:
        'Amount(N), Year(S), Net_Book_Value(C)'
        """
        elements = []

        for elem in elements_str.split(','):
            elem = elem.strip()

            # Match: ElementName(N/S/C)
            match = re.match(r'(.+?)\(([NSC])\)', elem)

            if match:
                elements.append({
                    'name': self.sanitize_name(
                        match.group(1).strip()
                    ),
                    'type': self.parse_element_type(
                        match.group(2)
                    )
                })
            elif elem:
                elements.append({
                    'name': self.sanitize_name(elem),
                    'type': 'Numeric'
                })

        return elements

    # ==========================================
    # NOTE PARSING
    # ==========================================

    def parse_note_content(self, note):
        """
        Parse note content to extract:
        - Consolidation rules (edges + weights)
        - Source references
        """
        result = {
            'class': note['class'],
            'edges': [],
            'sources': {}
        }

        lines = note['content']
        current_element = None

        for line in lines:
            line = line.strip()

            # Detect consolidation rule:
            # + Total_Amount (weight: +1)
            cons_match = self.patterns[
                'consolidation_rule'
            ].search(line)
            if cons_match:
                element_name = self.sanitize_name(
                    cons_match.group(1)
                )
                weight = int(cons_match.group(2))

                # Find parent (Consolidated element)
                parent = self._find_consolidated_parent(
                    note['class']
                )
                if parent:
                    result['edges'].append({
                        'parent': parent,
                        'child': element_name,
                        'weight': weight
                    })
                continue

            # Detect source element reference
            src_elem_match = self.patterns[
                'source_element'
            ].search(line)
            if src_elem_match:
                current_element = self.sanitize_name(
                    src_elem_match.group(1)
                )
                if current_element not in result['sources']:
                    result['sources'][current_element] = {}
                result['sources'][current_element][
                    'element'
                ] = current_element
                continue

            # Detect source cube
            src_cube_match = self.patterns[
                'source_cube'
            ].search(line)
            if src_cube_match and current_element:
                if current_element not in result['sources']:
                    result['sources'][current_element] = {}
                result['sources'][current_element][
                    'cube'
                ] = src_cube_match.group(1)
                continue

            # Detect source dimension
            src_dim_match = self.patterns[
                'source_dimension'
            ].search(line)
            if src_dim_match and current_element:
                if current_element not in result['sources']:
                    result['sources'][current_element] = {}
                result['sources'][current_element][
                    'dimension'
                ] = src_dim_match.group(1)
                continue

        return result

    def _find_consolidated_parent(self, class_name):
        """Find consolidated element in dimension"""
        if class_name in self.dimensions:
            dim = self.dimensions[class_name]
            for hierarchy in dim['hierarchies']:
                for element in hierarchy['elements']:
                    if element['type'] == 'Consolidated':
                        return element['name']
        return None

    # ==========================================
    # MAIN PARSER
    # ==========================================

    def parse(self, puml_file):
        """Main parse function"""

        logger.info(f"Parsing file: {puml_file}")

        try:
            with open(puml_file, 'r') as f:
                lines = f.readlines()
        except FileNotFoundError:
            logger.error(f"File not found: {puml_file}")
            raise

        current_section = None  # Leaf or Consolidated

        for line_num, line in enumerate(lines, 1):
            line = line.strip()

            # Skip empty lines and special tags
            if not line or line in [
                '@startuml', '@enduml', '{', '}'
            ]:
                continue

            # Skip pure comments
            if line.startswith("'"):
                continue

            # ===== NOTE HANDLING =====
            note_end = self.patterns['note_end'].search(line)
            if note_end and self.in_note:
                self.notes.append(self.current_note)
                self.in_note = False
                self.current_note = None
                continue

            if self.in_note:
                self.current_note['content'].append(line)
                continue

            note_start = self.patterns['note_start'].search(line)
            if note_start:
                self.in_note = True
                self.current_note = {
                    'class': note_start.group(1),
                    'content': []
                }
                continue

            # ===== CLASS DETECTION =====

            # Detect Cube
            cube_match = self.patterns['cube'].search(line)
            if cube_match:
                self.current_class = cube_match.group(1)
                self.current_type = 'cube'
                current_section = None
                self.cubes[self.current_class] = {
                    'name': self.current_class,
                    'description': '',
                    'specific_dimensions': [],
                    'shared_dimensions': [],
                    'related_cubes': []
                }
                logger.info(
                    f"Found Cube: {self.current_class}"
                )
                continue

            # Detect Dimension
            dim_match = self.patterns['dimension'].search(line)
            if dim_match:
                self.current_class = dim_match.group(1)
                self.current_type = 'dimension'
                current_section = None
                self.dimensions[self.current_class] = {
                    'name': self.current_class,
                    'type': 'Regular',
                    'description': '',
                    'hierarchies': [
                        {
                            'name': self.current_class,
                            'elements': [],
                            'edges': []
                        }
                    ]
                }
                logger.info(
                    f"Found Dimension: {self.current_class}"
                )
                continue

            # ===== ATTRIBUTE PARSING =====

            # Detect separator (--Leaf Elements--)
            sep_match = self.patterns['separator'].search(line)
            if sep_match:
                current_section = sep_match.group(1).strip()
                continue

            # Detect Description
            desc_match = self.patterns['description'].search(line)
            if desc_match and self.current_class:
                desc = desc_match.group(1).strip()
                if self.current_type == 'cube':
                    self.cubes[
                        self.current_class
                    ]['description'] = desc
                else:
                    self.dimensions[
                        self.current_class
                    ]['description'] = desc
                continue

            # Detect Type (Measures/Regular)
            type_match = self.patterns['type'].search(line)
            if type_match and self.current_type == 'dimension':
                self.dimensions[
                    self.current_class
                ]['type'] = type_match.group(1)
                continue

            # Detect typed element
            # (+Amount: Numeric / +Net_Book_Value: Consolidated)
            elem_typed_match = self.patterns[
                'element_typed'
            ].search(line)
            if elem_typed_match and \
               self.current_type == 'dimension':
                elem_name = self.sanitize_name(
                    elem_typed_match.group(1)
                )
                elem_type = elem_typed_match.group(2)
                self.dimensions[self.current_class][
                    'hierarchies'
                ][0]['elements'].append({
                    'name': elem_name,
                    'type': elem_type
                })
                logger.info(
                    f"  Element: {elem_name} ({elem_type})"
                )
                continue

            # Detect inline elements
            # (+elements: Amount(N), Year(S))
            elem_match = self.patterns['elements'].search(line)
            if elem_match and self.current_type == 'dimension':
                elements = self.parse_inline_elements(
                    elem_match.group(1)
                )
                self.dimensions[self.current_class][
                    'hierarchies'
                ][0]['elements'].extend(elements)
                continue

            # ===== RELATIONSHIP PARSING =====

            # Detect Composition (*--)
            comp_match = self.patterns['composition'].search(line)
            if comp_match:
                self.relationships.append({
                    'type': 'composition',
                    'source': comp_match.group(1),
                    'target': comp_match.group(2),
                    'label': (
                        comp_match.group(3) or ''
                    ).strip()
                })
                continue

            # Detect Aggregation (o--)
            agg_match = self.patterns['aggregation'].search(line)
            if agg_match:
                self.relationships.append({
                    'type': 'aggregation',
                    'source': agg_match.group(1),
                    'target': agg_match.group(2),
                    'label': (
                        agg_match.group(3) or ''
                    ).strip()
                })
                continue

            # Detect Dependency (..>)
            dep_match = self.patterns['dependency'].search(line)
            if dep_match:
                self.relationships.append({
                    'type': 'dependency',
                    'source': dep_match.group(1),
                    'target': dep_match.group(2),
                    'label': (
                        dep_match.group(3) or ''
                    ).strip()
                })
                continue

        # Process notes after full parse
        self._process_notes()

        logger.info("Parsing complete!")
        return self

    # ==========================================
    # NOTE PROCESSING
    # ==========================================

    def _process_notes(self):
        """Apply note content to dimensions"""
        for note in self.notes:
            parsed = self.parse_note_content(note)
            class_name = parsed['class']

            if class_name not in self.dimensions:
                continue

            dim = self.dimensions[class_name]

            # Add edges from consolidation rules
            if parsed['edges']:
                dim['hierarchies'][0]['edges'].extend(
                    parsed['edges']
                )
                logger.info(
                    f"Added {len(parsed['edges'])} edges "
                    f"to {class_name}"
                )

            # Add source references to elements
            if parsed['sources']:
                for elem in dim['hierarchies'][0]['elements']:
                    if elem['name'] in parsed['sources']:
                        elem['source'] = parsed['sources'][
                            elem['name']
                        ]
                        logger.info(
                            f"Added source reference to "
                            f"{elem['name']} in {class_name}"
                        )

    # ==========================================
    # JSON BUILDER
    # ==========================================

    def build_json(self):
        """Build JSON structure from parsed data"""

        # Identify shared dimensions
        # (used by more than one cube via aggregation)
        dim_usage = {}
        for rel in self.relationships:
            if rel['type'] == 'aggregation':
                dim = rel['target']
                dim_usage[dim] = dim_usage.get(dim, 0) + 1

        shared_dim_names = [
            d for d, count in dim_usage.items()
            if count > 1
        ]

        logger.info(
            f"Shared dimensions: {shared_dim_names}"
        )

        result = {
            "model": {
                "name": "TM1 Model",
                "description": "Auto-generated from PlantUML",
                "version": "1.0"
            },
            "shared_dimensions": [],
            "cubes": []
        }

        # Add shared dimensions
        for dim_name in shared_dim_names:
            if dim_name in self.dimensions:
                result['shared_dimensions'].append(
                    self.dimensions[dim_name]
                )

        # Process cubes
        for cube_name, cube in self.cubes.items():
            for rel in self.relationships:
                if rel['source'] == cube_name:
                    target = rel['target']

                    # Composition → specific dim or related cube
                    if rel['type'] == 'composition':
                        if target in self.dimensions:
                            # Don't duplicate shared dimensions
                            if target not in shared_dim_names:
                                cube[
                                    'specific_dimensions'
                                ].append(
                                    self.dimensions[target]
                                )
                        elif target in self.cubes:
                            cube['related_cubes'].append({
                                'name': target,
                                'relationship': rel['label']
                            })

                    # Aggregation → shared dimension
                    elif rel['type'] == 'aggregation':
                        if target not in cube[
                            'shared_dimensions'
                        ]:
                            cube['shared_dimensions'].append(
                                target
                            )

            result['cubes'].append(cube)

        return result

    # ==========================================
    # VALIDATION
    # ==========================================

    def validate(self, data):
        """Validate generated JSON"""
        errors = []
        warnings = []

        # Collect all dimension names
        all_dims = [
            d['name'] for d in data['shared_dimensions']
        ]
        for cube in data['cubes']:
            for dim in cube['specific_dimensions']:
                all_dims.append(dim['name'])

        # Check shared dimension references
        for cube in data['cubes']:
            for shared in cube['shared_dimensions']:
                if shared not in all_dims:
                    errors.append(
                        f"Cube '{cube['name']}' references "
                        f"unknown dimension '{shared}'"
                    )

        # Check element source references
        for cube in data['cubes']:
            for dim in cube['specific_dimensions']:
                for hierarchy in dim['hierarchies']:
                    for elem in hierarchy['elements']:
                        if 'source' in elem:
                            src_cube = elem['source'].get(
                                'cube'
                            )
                            if src_cube and src_cube not in [
                                c['name']
                                for c in data['cubes']
                            ]:
                                errors.append(
                                    f"Element '{elem['name']}' "
                                    f"references unknown cube "
                                    f"'{src_cube}'"
                                )

        # Check for spaces in names
        for cube in data['cubes']:
            if ' ' in cube['name']:
                warnings.append(
                    f"Cube name has spaces: '{cube['name']}'"
                )

        return errors, warnings

    # ==========================================
    # EXPORT
    # ==========================================

    def to_json(self, output_file):
        """Export to JSON file with validation"""
        data = self.build_json()

        # Validate
        errors, warnings = self.validate(data)

        if warnings:
            for w in warnings:
                logger.warning(w)

        if errors:
            for e in errors:
                logger.error(e)
            raise ValueError(
                f"Validation failed with {len(errors)} errors!"
            )

        # Write JSON
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"JSON exported to: {output_file}")
        return data

    def print_summary(self, data):
        """Print model summary"""
        print("\n" + "="*50)
        print(" MODEL SUMMARY")
        print("="*50)
        print(f"\nShared Dimensions: "
              f"{len(data['shared_dimensions'])}")
        for dim in data['shared_dimensions']:
            elem_count = sum(
                len(h['elements'])
                for h in dim['hierarchies']
            )
            print(f"{dim['name']} "
                  f"({elem_count} elements)")

        print(f"\nCubes: {len(data['cubes'])}")
        for cube in data['cubes']:
            dim_count = (
                len(cube['specific_dimensions']) +
                len(cube['shared_dimensions'])
            )
            print(f" {cube['name']} "
                  f"({dim_count} dimensions)")
            for dim in cube['specific_dimensions']:
                elem_count = sum(
                    len(h['elements'])
                    for h in dim['hierarchies']
                )
                cons_count = sum(
                    1 for h in dim['hierarchies']
                    for e in h['elements']
                    if e['type'] == 'Consolidated'
                )
                edge_count = sum(
                    len(h['edges'])
                    for h in dim['hierarchies']
                )
                print(
                    f"{dim['name']}: "
                    f"{elem_count} elements, "
                    f"{cons_count} consolidated, "
                    f"{edge_count} edges"
                )
        print("="*50 + "\n")


# ==========================================
# RUN PARSER
# ==========================================

def main():
    try:
        parser = PlantUMLParser()

        # Parse UML file
        parser.parse('fixed_assets.puml')

        # Generate and export JSON
        result = parser.to_json('tm1_spec.json')

        # Print summary
        parser.print_summary(result)

        print("Successfully generated tm1_spec.json!")

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
    except ValueError as e:
        logger.error(f"Validation error: {e}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise


if __name__ == "__main__":
    main()
