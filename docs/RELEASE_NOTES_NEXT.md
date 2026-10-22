This update adds support for NVIDIA RTX 50 series (Blackwell) graphics cards.
The installer now selects the PyTorch build that matches your graphics card,
and if the installed build does not fit the card, the app continues the
conversion on the CPU instead of crashing. After updating, open Install
engines → Repair once; it downloads about 3 GB.

### The update prompt shows what changed again

This update also fixes the notes panel in the update prompt, which came up
empty when version 3.23.0 was offered.

When a new version is offered, the app shows a short summary of what changed
before you press the button. That summary stayed blank for 3.23.0. The notes
were published correctly on the download page, so nothing was lost, but the
app could not read them once the notes were split into sections. It reads
them again now.
