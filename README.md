# tm1-uml-generator
Tool for creating TM1 objects from PlantUML diagrams
# TM1 UML Generator

## Description
This project generates **TM1 objects** (Cubes, Dimensions, Elements)
from **PlantUML** class diagrams.

## Project Structure
- `uml/` - PlantUML source file and diagram image
- `src/` - Python scripts for parsing and TM1 object creation
- `json/` - Intermediate JSON model

## Workflow
UML (.puml) → JSON → TM1 Objects

## Requirements
- Python 3.8+
- TM1py
- PlantUML

## Installation
pip install -r requirements.txt

## Usage
# Step 1: Parse UML to JSON
python src/uml_parser.py

# Step 2: Create TM1 objects from JSON
python src/tm1_object_creator.py
