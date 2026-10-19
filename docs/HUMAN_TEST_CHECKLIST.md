# Human test checklist

Things the AI cannot verify. Tick only after trying it yourself on a real machine. `/release-checklist` adds per-release sections above the standing list.

## Standing checks
- [ ] Scan a real library (10k+ photos, mixed formats, network/external drive) - speed, no freezes, counts look right
- [ ] Duplicates: review exact + similar groups on real photos; quarantine; restore via Activity log undo
- [ ] Organize: preview then apply on a copy of a library; undo restores every file to its original path
- [ ] Backup to an external disk: first run, incremental run, change/delete files on source, confirm `_Deleted_Archive\<date>` holds them; unplug mid-copy and re-run
- [ ] "Send to Recycle Bin" really lands in the Windows Recycle Bin
- [ ] Health check flags a genuinely corrupt photo
- [ ] Import from a camera card / phone folder
- [ ] AI: first-time model download, search quality, people/face clustering on real faces, GPU (DirectML) vs CPU
- [ ] Look and feel: high-DPI / 150% scaling, dark mode, window resize, every page visually
- [ ] Release exe: unzip `PhotoVault-vX.Y.Z-win64.zip` on a clean Windows machine, launches without Python installed
- [ ] AI auto tags: after indexing a real library (or `worker retag`), judge tag quality: wrong/missing labels, too many/few per photo; tune thresholds in `photovault/ai/vocab.py` (cosine, default 0.10 is a guess)
- [ ] AI first-run setup (#4): on a clean Windows machine with Python 3.13 installed, unzip the exe, click "Enable AI features" (progress, cancel, re-click resumes), then index a few photos and search; also confirm the message when Python 3.13 is missing, and that the model URLs in `photovault/ai/setup_env.py` resolve
- [ ] AI events: run the `events` command on a real library and judge the grouping (days/trips split sensibly, 7 h gap and 50 km thresholds feel right, names readable, a renamed event survives a recompute)
- [ ] AI Tags page (#17): on a real indexed library, browse tags/events, accept/reject auto tags, "Accept all above", rename an event, Compute events, open an event from the Events tab, use tag chips in Search; check layout, readability and responsiveness with thousands of tags
- [ ] Umbra theme + zoom (#27): on a real machine check the look at 70%, 100% and 200% zoom (Ctrl+-, Ctrl+=, Ctrl+0; no clipped text, sidebar aligned, thumbnails sharp), at 125/150% Windows scaling on a high-DPI screen, and in the light theme (Settings > Theme); confirm the zoom survives a restart and the exe shows the sidebar icons (QtSvg bundled)
- [ ] Help page (#28): in the built exe (clean machine) press F1: the guide and What's new render (not the 'file was not found' text), section index jumps work, About dialog shows icon/version/credits, both themes and 70%/200% zoom look right
- [ ] XMP sidecar export (#3): on a COPY of a few real photos with accepted tags and named people, run Tags > Export to XMP sidecars; check the counts dialog, that `photo.jpg.xmp` appears and the photo is unchanged, that digiKam / Immich / Lightroom show the keywords and People|Name hierarchy, that a second run skips existing sidecars, and that Activity log > Undo moves them to quarantine (and Restore brings them back)
