{
  description = "A Matrix bot that shows your Last.fm stats directly in your rooms";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };

  outputs =
    {
      self,
      nixpkgs,
    }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in
    {
      devShells = eachSystem (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              self.packages.${system}.default
            ];
          };
        }
      );
      packages = eachSystem (system: {
        default = self.packages.${system}.fmatrix;
        fmatrix = nixpkgs.legacyPackages.${system}.callPackage ./package.nix { };
      });
    };
}
