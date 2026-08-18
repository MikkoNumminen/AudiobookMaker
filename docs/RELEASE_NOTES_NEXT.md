### Continue an interrupted conversion

If a conversion stops before it finishes, the app now shows a **Continue**
button. It picks up where it left off and reuses the parts already made, so
you do not have to add the file again or wait through the whole book a second
time. The button tells you how much is already done.

If a conversion stops while you are away, the app tries once more on its own
and says so afterwards, so a run that failed twice is not mistaken for a run
that simply took longer.

### A long book is no longer cut short

A conversion that ran past 12 hours used to be stopped by the app itself. That
limit is gone. The app now watches for a conversion that has genuinely stopped
responding, rather than one that is simply taking a long time. A book-length
conversion on a mid-range graphics card can take 14 hours or more, and that is
now a normal job rather than one that gets killed near the end.

### You can hear where a new chapter starts

Headings now get a clear pause on both sides, so a new chapter or section is
audible instead of running straight into the text. Previously the narrator
read the title and continued into the first sentence without a break, which
made it hard to tell where anything began.

This works in Finnish and English, and for PDF, EPUB, Word and plain text
files.

### Long chapters are saved in parts

A very long chapter is now written as several numbered files instead of one
enormous one. You get audio you can listen to much sooner, and if something
goes wrong late in a conversion you keep the parts that were already finished.

### Finnish legal texts read correctly

Section signs, chapter and section references, court decisions and law
abbreviations are now read out as words instead of being skipped or read as
something else. For example `MK 2:1` is read as "maakaaren luku kaksi pykälä
yksi" rather than as a ratio.

### Fixes

- Silence at the start and end of each part is now trimmed. This had never
  actually worked before, so recordings should sound tighter throughout.
- Clock times are read as times. "Kello on 20:30" used to be read as a ratio,
  and a time like 20:05 lost its zero and came out as "twenty five".
- A conversion that fails now shows an error and re-enables the buttons,
  instead of leaving the app looking busy until you close it.
- The progress bar and time estimate no longer drift upward on long runs.
  They were counting discarded retries as though they were real work.

### One thing to know about upgrading

If you have a half-finished conversion, this version starts it over. The parts
already made cannot be matched to the new way of storing them.

After this, editing your text only redoes the parts that actually changed, so
fixing a typo no longer costs you the whole book. Changing the voice, or
updating the speech engine, still starts over, because the audio would not
match what came before it.
