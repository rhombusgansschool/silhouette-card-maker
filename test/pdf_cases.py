"""Shared test case definitions for create_pdf.py tests.

Imported by both generate_expected_images.py (to produce reference images)
and test_create_pdf.py (to run the parametrized pixel-comparison tests).
Adding a new test case here automatically covers both.
"""
import os

# Shared fixture paths
IMAGES_DIR = os.path.join('test', 'images')                    # card images used as input
BACK_DIR = os.path.join('test', 'basic', 'back')               # back image for all tests
DS_DIR = os.path.join('test', 'basic', 'double_sided')         # empty; no double-sided cards in tests
EXPECTED_DIR = os.path.join('test', 'expected_pdfs')           # pre-generated reference PNGs

# Each entry: (name, extra_cli_args)
# - name: identifies the test and maps to a subdirectory in EXPECTED_DIR
# - extra_cli_args: additional CLI arguments passed to create_pdf
TEST_CASES = [
    ('default', ['--paper_size', 'letter']),
    ('only_fronts', ['--paper_size', 'letter', '--only_fronts']),

    # Card sizes
    ('bridge', ['--paper_size', 'letter', '--card_size', 'bridge', '--only_fronts']),
    ('poker', ['--paper_size', 'letter', '--card_size', 'poker', '--only_fronts']),
    ('japanese', ['--paper_size', 'letter', '--card_size', 'japanese', '--only_fronts']),

    # Paper sizes
    ('tabloid', ['--paper_size', 'tabloid', '--only_fronts']),
    ('a4', ['--paper_size', 'a4', '--only_fronts']),
    ('a3', ['--paper_size', 'a3', '--only_fronts']),

    # Paper and card combinations
    ('tabloid-domino_square', ['--paper_size', 'tabloid', '--card_size', 'domino_square', '--only_fronts']),
    ('a4-bridge', ['--paper_size', 'a4', '--card_size', 'bridge', '--only_fronts']),

    # Other options
    ('ppi600', ['--paper_size', 'letter', '--ppi', '600']),
    ('a4-ppi600', ['--paper_size', 'a4', '--ppi', '600', '--only_fronts']),

    ('ppi600-quality100', ['--paper_size', 'letter', '--ppi', '600', '--quality', '100']),

    ('quality100', ['--paper_size', 'letter', '--quality', '100']),
    ('quality75', ['--paper_size', 'letter', '--quality', '75', '--only_fronts']),
    ('quality50', ['--paper_size', 'letter', '--quality', '50', '--only_fronts']),

    ('registration4', ['--paper_size', 'letter', '--registration', '4']),
    ('registration4-tabloid', ['--registration', '4', '--paper_size', 'tabloid']),

    ('show_outline', ['--paper_size', 'letter', '--show_outline']),
    ('show_outline-domino', ['--paper_size', 'letter', '--show_outline', '--card_size', 'domino']),

    ('label', ['--paper_size', 'letter', '--label', 'Test Label', '--only_fronts']),
    ('label-tabloid', ['--label', 'Test Label', '--paper_size', 'tabloid', '--only_fronts']),

    ('crop', ['--paper_size', 'letter', '--crop', '3mm', '--only_fronts']),
    ('extend_corners', ['--paper_size', 'letter', '--extend_corners', '10', '--only_fronts']),
    ('fit_crop', ['--paper_size', 'letter', '--card_size', 'domino', '--fit', 'crop', '--only_fronts']),
    ('skip', ['--paper_size', 'letter', '--skip', '0', '--skip', '4']),

    # Borderless templates
    ('borderless-letter-standard', ['--borderless', '--paper_size', 'letter', '--only_fronts']),
    ('borderless-a4-standard', ['--borderless', '--paper_size', 'a4', '--only_fronts']),
    ('borderless-letter-poker', ['--borderless', '--paper_size', 'letter', '--card_size', 'poker', '--only_fronts']),
    ('borderless-a4-poker', ['--borderless', '--paper_size', 'a4', '--card_size', 'poker', '--only_fronts']),
    ('borderless-tabloid-standard', ['--borderless', '--paper_size', 'tabloid', '--only_fronts']),
]
