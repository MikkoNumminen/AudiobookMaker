This update keeps the app in its own folder when you update from an older
version. If the app is installed in a folder with letters such as ä or ö in
its name, updating from version 3.24.0 or older used to move the app into a
new folder with a garbled name. The installer now notices this, installs
into the folder the app was already in, and starts the app from there.

### Why the older versions needed help

The update is started by the version you are replacing, and versions up to
3.24.0 passed folder names like "Äänikirjat" to the installer in a garbled
form ("├ä├ñnikirjat"). Version 3.24.1 fixed that for its own updates, but an
older version still uses its old way one last time when it updates. The new
installer recognises that garbled name and uses the real folder instead.

If an earlier update already moved your app into a garbled folder, it stays
in that folder from now on and the name is not garbled any further.
