# Kanji Neighbours

Click a kanji on a vocab card to see other reviewed words in the deck that share it.

## Card template

```html
<span class="kanji-neighbours-word">{{wordDictionaryForm}}</span>
```

`#word` still works if you already use it.

## Setup

1. Copy this folder into `%APPDATA%\Anki2\addons21\kanji_neighbours` (copy, not a junction)
2. Restart Anki
3. Tools → Add-ons → Kanji Neighbours → Config

```powershell
xcopy "C:\Users\turne\Projects\kanji-neighbours\*" "%APPDATA%\Anki2\addons21\kanji_neighbours\" /E /I /Y
```
