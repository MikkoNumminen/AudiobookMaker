This update fixes Install engines → Repair failing on computers where the app
is installed in a folder with special characters in its name. On those
computers Repair always stopped at its final check, so an RTX 50 series card
could never be switched over from the CPU to the graphics card. After
updating, open Install engines → Repair once more and it finishes normally.
