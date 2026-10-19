---
name: update-local
description: Rebuild Photo Vault from the latest main and install it over the local exe in %LOCALAPPDATA%\Programs\PhotoVault (shortcut re-pointed, old copy kept as .old). Use when asked to update, rebuild or reinstall the user's own app.
---

For the user's own machine only. Released builds update themselves in the app (Update button); use this for unreleased `main` or when the in-app updater is unavailable.

1. Make sure the working tree is clean and on `main`: `git status --short`, `git pull --ff-only`. Stop and ask if there are uncommitted changes.
2. Ask the user to close Photo Vault if `Get-Process PhotoVault` finds it (the build script refuses to run otherwise).
3. Run `powershell -File build.ps1 -InstallDir "$env:LOCALAPPDATA\Programs\PhotoVault"` (runs tests first, builds, mirrors `dist\PhotoVault` into the install dir, keeps the previous install as `PhotoVault.old`, re-points the Desktop "Photo Vault" shortcut).
4. Report the installed version (`photovault/__init__.py`) and tell the user to launch it once and check About. Delete `PhotoVault.old` only after the user confirms it works. Settings, index and photos live in `%LOCALAPPDATA%\PhotoVault` and are never touched.
