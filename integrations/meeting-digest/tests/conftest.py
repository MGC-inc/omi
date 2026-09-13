import os
import sys

# The package lives one level up; tests run without an install step.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
