Mistune 3.2.1 (BSD-3-Clause), bundled for offline deployment.
Source: https://github.com/lepture/mistune
Custom renderer and image layout live in guide.py.
One namespace-only patch in mistune/plugins/__init__.py resolves dynamic plugin imports under vendor.mistune rather than requiring a system installation. All parsing code is unchanged.
