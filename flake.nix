{
  description = "trvzb-firmware — reverse engineering and patching of the SONOFF TRVZB application MCU";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      nixpkgs,
      flake-utils,
      ...
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (pythonPackages: [
              pythonPackages.capstone
              pythonPackages.pytest
            ]))
            pkgs.curl
            pkgs.pkgsCross.arm-embedded.buildPackages.binutils
            pkgs.just
            pkgs.kubectl
          ];
        };
      }
    );
}
