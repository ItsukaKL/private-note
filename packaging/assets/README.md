# Icon Placement

Static icon assets live here:

- Root `icon.png`: project source-of-truth image used by the desktop client window icon
- `app.ico`: Windows executable icon used by PyInstaller

There is no in-repo icon generation step anymore.
If the root `icon.png` is replaced, update `app.ico` from that image before packaging.
