{
  pkgs,
}:

pkgs.python3Packages.buildPythonApplication rec {
  name = "fmatrix";
  src = ./.;

  pyproject = true;
  build-system = with pkgs.python3Packages; [
    setuptools
  ];

  dependencies = with pkgs.python3Packages; [
    aiohttp
    pylast
    python-dateutil
    aiosqlite
    pillow
    matrix-nio
  ];
}
