"""WY Wallet V2 stable entry point.

All production logic lives in normal importable modules under ``wywallet``.
There is no runtime source rewriting, exec, or monkey-patching.
"""

from wywallet.web import run

run()
