PY_FILES = __init__.py dotxsi.py xsi3_blender_importer.py xsi3_blender_exporter.py

# Folder name inside the zip = the add-on's module name in Blender. Kept as io_scene_dotXsi3 so an
# installed copy's preferences (enabled state) carry over.
PACKAGE = io_scene_dotXsi3

DOC = ja_xsi_tools_doc

ZIP_CONTENTS = $(PY_FILES) README.md

PEP8_FILES = $(PY_FILES) tests/*.py tests/tools/*.py

.PHONY: all format pep8 clean

all: build/$(PACKAGE).zip build/$(DOC).pdf

format:
	autopep8 --in-place $(PEP8_FILES)

pep8:
	pycodestyle --config=.pep8 $(PEP8_FILES)

build/$(PACKAGE).zip: $(ZIP_CONTENTS)
	rm -rf build/$(PACKAGE) $@
	mkdir -p build/$(PACKAGE)
	cp $(ZIP_CONTENTS) build/$(PACKAGE)
	(cd build; zip -r $(PACKAGE).zip $(PACKAGE))

build/$(DOC).pdf: $(DOC).tex
	mkdir -p build
	pdflatex --output-directory=build $(DOC).tex
	pdflatex --output-directory=build $(DOC).tex  # second pass for the table of contents

clean:
	rm -rf build
