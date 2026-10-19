# Photo Vault user guide

Button and page names below are copied from the app. Pages are listed in sidebar order.

## 0. Before you start

1. **Settings first.** On a fresh install the app starts with your `Pictures` folder as the library and shows a
   "Welcome - first run" notice on the Dashboard and Settings pages. Open **Settings**, check
   *Library folders (scanned)*, *Organized photos go to* and *Quarantine folder*, then click **Save settings**
   (this dismisses the notice). Choose a backup disk on the **Backup** page (*Backup to:* starts empty and
   **Back up now** will not run until you pick one).
2. Recommended order: **Settings** > **Dashboard** (Scan library) > **Health check** > **Duplicates** >
   **Organize** > **Backup** > **Quarantine** cleanup > the AI pages (**AI Index**, **Search**, **People**).
3. Run **Backup** before any big step (Organize, emptying the quarantine).

### Safety rules

- Nothing is deleted directly. Removed files go to the quarantine folder; **Send selected to Recycle Bin...** /
  **Send ALL shown to Recycle Bin...** on the Quarantine page is the only way files leave your disk.
- Duplicates are re-verified byte for byte just before they are moved.
- Organize and quarantine moves are recorded in the **Activity log** and can be undone.
- Backups never delete anything on the backup disk (see Backup).
- Exception: **Remove selected empty folders** (Health check) deletes empty folders directly, without a prompt.
  They contain no files, but it is not undoable.

## Dashboard
Overview cards (Photos, Videos, Library size, Without a date, Duplicates found, In quarantine, Last backup, Free
space) and charts. **Scan library** re-reads your library folders (a quick scan also runs at every start).
**Export report...** saves the overview. Run a scan after changing folders in Settings.

## Duplicates
Two tabs. **Exact copies**: click **Find exact duplicates**. **Similar photos**: click **Find similar photos**
(sensitivity is set in Settings). Pick a group, choose the copy to keep with **Keep this one** (hand-picked folders
are preferred automatically), then **Move the other copies to quarantine**. **Show in folder** opens a file in
Explorer. **Not duplicates - ignore this group** hides a group for good. **Quarantine extras in ALL shown
groups...** does it for every group under the current *Folder:* filter, after a confirmation.

## Organize
Sorts everything under the organized folder (Life\Year\Year-Month, Chat Media, Screenshots, Gaming, Projects,
Documents...) according to the folder rules in Settings. Click **1. Build plan (preview)**, read it, then
**2. Apply plan...**. **Undo last organize** moves files back. Back up first.

## Import
For phones, cards and USB sticks. Phones: copy the phone's DCIM folder to the PC in File Explorer first, then
choose it. Pick the *Folder:* (**Browse...**), type a *Device name:*, optionally **Count files**, then
**Import to Inbox**. Photos you already have are skipped (listed under "Skipped because you already have them"),
every copy is verified. **Open Inbox folder** shows the result. The result banner tells you what to do with the
source:

- **Green** - everything is safely in your library; deleting from the source is safe (ideally after your next backup).
- **Orange** (cancelled) - do NOT delete anything from the source yet.
- **Red** - some files failed to import; do NOT delete the source. See the details below the banner.

## Backup
Choose *What to back up* (**Add folder...**, **Remove selected**) and *Backup to:* (**Browse...**). The status line
shows whether the disk is connected. **Preview** shows what would change. **Back up now** mirrors the library to the
disk; every copied file is hash-verified. Files you deleted or changed are first moved to
`_Deleted_Archive\<date>` on the backup disk; nothing on the backup is ever deleted. **Quick verify** and
**Full verify (slow)** compare backup and library (green = all match, red = run **Back up now**). **Restore a
file...** / **Restore a folder...** copy things back. *History* lists past backups.

## Health check
**Run health check** finds damaged photos (tick the "Fully open every photo..." box for a deeper, slower check),
empty files, junk, tiny images, photos without a date and empty folders, each in its own tab. Select rows, then
**Select all**, **Show in folder**, **Quarantine selected** (moves to quarantine, undoable),
**Set date for selected...** (no-date tab), or **Remove selected empty folders** (deletes directly, no prompt).

## Browse
Filter by *Year:*, month, *Folder:*, type (Photos & videos / Photos / Videos) or a file-name search, click **Show**,
**Load more** for more. Double-click opens a file.

## Search (AI)
Describe a photo in English, French, German or Arabic, or type a name given in People, then **Search**. Needs the
AI index; without it the page says "AI not set up".

## People (AI)
**Find groups** clusters faces. Type a name per group and **Save name**, or **Not a person**. **Match known people**
labels new faces. Search by name afterwards.

## Tags (AI)
Lists tags by kind (people, events, things) with photo counts and how many auto tags still await review. Pick a tag to see its photos; auto tags are shown in italic with their score. Select a photo to accept or reject its auto tags, or add your own tag. "Accept all above" confirms every auto tag of the selected thing at or above a score. "Compute events" groups photos into events (date gaps and GPS jumps); rename an event to keep its name. The Events tab lists events with date range and photo count; double-click one to open its photos. People tags come from the People page and are read-only here.

Search also has tag chips: type a tag name under the search box and press Enter; photos must carry every chip. Tag names typed in the query itself (e.g. "beach") also act as filters.

## AI Index
**Index new photos** reads every photo once (originals are never changed). Advanced: `ai_model` (`base-256`, `base-384`, `so400m-384`) and `ai_fit` (`squash`, `pad`) in `settings.json` choose a larger/other search model; after changing them click **Enable AI features** (downloads it) and re-index, which re-reads the whole library. **Quick test (200 photos)** tries it on a
small batch. **Stop** halts it; you can continue later.

## Quarantine
Lists files the app moved out of your library. **Restore selected** puts them back. **Send selected to Recycle
Bin...** / **Send ALL shown to Recycle Bin...** are the only removals; they go to the Windows Recycle Bin (very big
files may be deleted permanently by Windows). **Open quarantine folder** shows the folder. Do this after a backup.

## Activity log
Everything the app changed. Select a move (organize, quarantine) and click **Undo selected...**.

## Settings
- *Folders*: *Library folders (scanned)* (**Add...**, **Remove**), *Organized photos go to:*, *Quarantine folder:*.
- *Behaviour*: *Similar-photo sensitivity (1 strict - 7 loose)*, *Remind me to back up after:* N days, *Theme:*.
- *Folder rules*: each top-level folder name (wildcards `*`) maps to a category (raw, curated, screenshots,
  projects, documents, other, skip, chat:App, gaming:Name); first match wins, unmatched folders count as raw.
  **Add rule**, **Remove selected rule**, **Move up**, **Move down**, **Reset to defaults**. The built-in rules were
  written for the author's folder names; edit them for your library.
- **Save settings** stores everything. Existing settings files are never overwritten at startup.

## Where things live
- Settings, index and journal: `%LOCALAPPDATA%\PhotoVault` (`settings.json`, `library.db`, `thumbs\`). Set
  `PHOTOVAULT_HOME` to move it.
- AI data (`ai.db`, models): the `data\` folder next to the program (`PHOTOVAULT_AI_DATA` overrides).
- Quarantine: the folder set in Settings (default `Pictures\_Duplicates_To_Review`).

## Look and zoom
Photo Vault uses a dark, Umbra-style look (true-black surfaces, one indigo accent, outline icons in the sidebar). Choose the light variant under Settings > Behaviour > Theme.

Make everything bigger or smaller with the keyboard, on any page: **Ctrl+=** (or Ctrl++) zoom in, **Ctrl+-** zoom out, **Ctrl+0** reset. Range 70% to 200% in 10% steps; the current zoom shows briefly in the status bar and is remembered between runs.

## Help and About
Open **Help** in the sidebar (or press **F1** on any page) to read this guide inside the app; it also shows *What's new*. It follows the theme and zoom. **About Photo Vault** (sidebar footer, or the button on the Help page) shows the version, the project link and the credits for the optional AI models.
