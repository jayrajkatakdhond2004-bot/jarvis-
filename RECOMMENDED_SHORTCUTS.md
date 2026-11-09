# Recommended Shortcuts

Create these in the **Shortcuts** app. Use the names exactly as written: Jarvis's `run_shortcut` tool already knows them. Build them on your Mac or your iPhone; iCloud syncs them to both.

## What actually reaches your iPhone

`shortcuts run` **runs the shortcut on the Mac**. iCloud syncs the shortcut itself to your other devices, not its execution. So a "Set Timer" shortcut started by Jarvis sets a timer on the Mac, not on your phone.

Your iPhone and iPad still pick up the result in three ways:

1. **iCloud-synced data.** Reminders, Calendar, Notes and Messages created on the Mac show up on the phone within seconds.
2. **Cross-device features.**
   - Focus modes sync when "Share Across Devices" is on.
   - HomeKit scenes run from the Mac's Home app.
   - Phone calls go through your iPhone via Continuity.
3. **The relay pattern**, for anything that must run *on* the phone (see [Relay to iPhone](#11-relay-to-iphone-optional-advanced)). The Mac sends you an iMessage containing a codeword, and an iPhone Personal Automation runs a shortcut when that message arrives.

## Passing input

Jarvis passes its input as a text file. Start any shortcut that takes input with **Receive [Text, Files] input from Share Sheet / Quick Actions**, then **Get Text from [Shortcut Input]**.

If the shortcut should report something back, for example the weather, end it with **Stop and Output [result]**. Jarvis reads that output and speaks it.

---

### 1. `Jarvis Text`
Sends an SMS or iMessage through the Messages app (SMS needs Text Message Forwarding turned on in iPhone Settings → Messages).

1. Get Text from Shortcut Input. The input looks like `Pepper: Running late`.
2. Split Text by custom `:`. Get Item 1 as the recipient and Items 2… as the message.
3. **Send Message** [Message] to [Recipient]. Turn "Show When Run" off.
4. Stop and Output `Sent`.

> Jarvis already has a built-in `send_imessage` tool. Use this shortcut for SMS (green-bubble) contacts. Jarvis always asks you to confirm first.

### 2. `Jarvis Set Timer`
1. Get Text from Shortcut Input (minutes, e.g. `10`).
2. **Start Timer** for [Input] minutes. On macOS Sonoma and later this is in the Clock app.
3. Stop and Output `Timer set for [Input] minutes`.

> To set the timer on the iPhone instead, use the relay pattern.

### 3. `Jarvis Play Playlist`
1. Get Text from Shortcut Input (playlist name).
2. **Find Playlists** where Name contains [Input]. Limit to 1.
3. **Play Music** [Playlists]. Shuffle is optional.
4. Stop and Output the playlist's Name.

### 4. `Jarvis Find My iPhone`
No Shortcuts action can play a sound on another device directly. Two options:

- **Simple:** Open URL `https://www.icloud.com/find`, then click "Play Sound" yourself.
- **Hands-free:** relay the codeword `JARVIS FIND` to the iPhone. Its automation sets the volume to 100% and plays a sound or alarm.

### 5. `Jarvis Focus`
1. Get Text from Shortcut Input (`Work`, `Do Not Disturb`, `Sleep`, `off`).
2. If Input is `off`: **Turn Do Not Disturb Off**. Otherwise: **Set Focus** [Input] **On** until Turned Off.
3. Stop and Output `Focus: [Input]`.

> Focus syncs to your iPhone and iPad if you turn on System Settings → Focus → **Share Across Devices**.

### 6. `Jarvis Note`
1. Get Text from Shortcut Input.
2. **Create Note** with [Input] in folder "Jarvis".
3. Stop and Output `Noted`.

Syncs to all your devices through iCloud Notes.

### 7. `Jarvis Home Scene`
1. Get Text from Shortcut Input (scene name, e.g. `Good Night`).
2. **Control [Home]** → Run Scene → choose *Ask Each Time* / use [Input]. Alternatively, build one `If` branch per scene.
3. Stop and Output `Scene [Input] activated`.

Runs through the Home app on your Mac, so it needs HomeKit and a home hub.

### 8. `Jarvis Directions`
1. Get Text from Shortcut Input (destination).
2. **Show Directions** to [Input] using Maps (driving).
3. Stop and Output `Opening directions to [Input]`.

> Recent Maps destinations sync to your iPhone. To start navigation on the phone directly, use the relay pattern.

### 9. `Jarvis Weather`
1. **Get Current Weather** at Current Location.
2. **Get Weather Forecast** (daily). Get the first item.
3. Text: `Currently [Condition], [Temperature]. High [High], low [Low]. [Precipitation Chance] chance of rain.`
4. **Stop and Output** [Text]. Jarvis reads it out in its own words.

### 10. `Jarvis Call`
1. Get Text from Shortcut Input (contact name or number).
2. **Call** [Input]. On a Mac this places the call through your iPhone (Continuity: iPhone Settings → Phone → Calls on Other Devices). Use **FaceTime** for FaceTime audio.
3. Stop and Output `Calling [Input]`.

Jarvis confirms before running this one.

### 11. Relay to iPhone (optional, advanced)
For things that must happen **on** the iPhone, such as a phone timer, an alarm, a ringing iPhone, or phone-only apps.

**On the Mac**, create `Jarvis Phone`:
1. Get Text from Shortcut Input (e.g. `TIMER 10`).
2. **Send Message** `JARVIS [Input]` to *your own* phone number or Apple ID.
3. Stop and Output `Sent to iPhone`.

**On the iPhone**, go to Shortcuts → Automation → **+** → **Message**. Set "Message Contains" to `JARVIS` and "Sender" to yourself, then choose **Run Immediately**. The automation does:
1. Get the message text. Split it by spaces and match on the second word.
2. `TIMER`: Start Timer for [third word] minutes.
   `FIND`: Set Volume 100% and Play Sound.
   `ALARM`: Create Alarm…
   and so on.

Then just ask: "Jarvis, set a ten-minute timer on my phone." Claude calls `run_shortcut("Jarvis Phone", "TIMER 10")`. Its name contains no send-like word, so it runs without asking. Add `send` to the name if you want Jarvis to confirm every relay.

---

## Naming tips
- Prefix every Jarvis shortcut with `Jarvis` so they're easy to find. Claude can also find them with `list_shortcuts`.
- If you add more, add their names to the `run_shortcut` description in `tools.py` so Claude knows they exist.
- Shortcuts with **Text, Call, Message, Mail, Send, Delete, Pay, Lock** or **Erase** in the name always require a spoken "yes" first.
