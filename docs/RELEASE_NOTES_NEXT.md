Chatterbox voices are now about three to four times faster on an NVIDIA graphics card. Until now the graphics card spent most of its time waiting, so a newer card was hardly faster than an old one. The app now keeps the card busy, and it writes in the log which card it is using. The voice and the audio are exactly the same as before. Press Update to get it; no Repair is needed.

### What changed

Speech is made in many small steps, and the app used to hand each step to the graphics card separately. Handing over the steps took longer than the card needed to do them, so the card was busy only about a third of the time. The app now prepares the steps in advance and passes them to the card in one go. On the card used for testing, one minute of audio used to take a little over a minute to make. It now takes about 17 seconds.

How much faster your own conversion gets depends on your graphics card. The log shows a number called RTF on every progress line: it is the time a part took to make divided by how long it plays. Smaller is faster.

### Which graphics card is used

When Convert starts, the log now names the graphics card the app is using, for example "GPU in use: NVIDIA GeForce RTX 5060 Ti". If the computer has more than one NVIDIA card, for example an old card left in next to a new one, the app lists every card and uses the most powerful one it can run on. Before, it always took the first card in the list, which could be the old one.

### If something goes wrong

Before it starts on your text, the app checks that the faster method works on your card. If it does not, the app uses the old method, which is slower but works as before. In the rare case that the check fails in a way that needs a fresh start, the conversion stops with a message asking you to press Convert again. The parts already made are kept, and from then on that computer uses the old method.
