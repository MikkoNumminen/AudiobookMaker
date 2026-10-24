This update fixes three problems. Updates no longer move the app into a
folder with a garbled name when the install folder has letters such as ä or ö
in it. Install engines → Repair now finishes on those computers too, so an
RTX 50 series card can switch from the CPU to the graphics card. Edge voices
work again.

### Updates keep the app in its own folder

If the app was installed in a folder like "Äänikirjat", an update installed
the new version into a new folder with a garbled name such as "├ä├ñnikirjat"
and started the app from there. Each later update garbled the name a little
more. Updates now keep the folder name exactly as it is.

If your app already moved into a garbled folder, download the installer from
the releases page and install it into the folder you want. Before you delete
the garbled folder, move out any audiobooks you made since the update: the
app saves them next to itself, so they are in that folder. Your engines and
voices are stored elsewhere and are not affected.

### Repair works in folders with unusual names

Repair ends with a check that the engine starts. On computers where the app
folder had a name the check could not print, the check always failed, so
Repair could never install the right version of PyTorch for the graphics
card. After updating, open Install engines → Repair once more and it
finishes normally.

### Edge voices work again

Microsoft changed its speech service, and Edge voices stopped with an error
("403"). This update includes a newer version of the Edge speech library that
works with the service again.
