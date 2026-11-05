# Location Spoofer

A free app for Mac and Windows that sets your iPhone's location to any spot you pick on a map. While it's on, your whole phone reports that location, including **Find My**, so people you share your location with see the spot you chose.

It uses Apple's built-in developer "simulate location" feature, the same one app developers use for testing. There's no jailbreak, and nothing gets installed on the phone.

> **Keep the app running while you spoof.** The fake location only lasts while the app is open and your computer is awake. Quitting the app, or clicking **Stop spoofing**, puts your phone back to its real location.

## Download

Go to the [Releases page](../../releases) and download:
- **Mac:** `LocationSpoofer-mac.zip`. Unzip it to get `LocationSpoofer.app`.
- **Windows:** `LocationSpoofer-windows.zip`. Unzip it and run `LocationSpoofer.exe`.

The app is free and unsigned, so your computer will warn you the first time you open it:
- **Mac:** double-click `LocationSpoofer.app`. When macOS blocks it, open **System Settings → Privacy & Security**, scroll down, click **Open Anyway** next to LocationSpoofer, and confirm. You only do this once. If macOS says the app "is damaged", run this once in Terminal instead:
  `xattr -dr com.apple.quarantine ~/Downloads/LocationSpoofer.app`
- **Windows:** when SmartScreen appears, click **More info**, then **Run anyway**.

## One-time setup

1. **Windows only:** install **iTunes from apple.com/itunes**, the classic installer. The Microsoft Store versions of iTunes and "Apple Devices" won't find your phone over Wi-Fi.
2. Open the app. It opens a map in your browser.
3. Plug your iPhone in with a cable, unlock it, and tap **Trust** if asked.
4. Choose your phone in the app and click **Prepare phone**.
5. If the app asks you to turn on Developer Mode:
   1. On the phone, go to **Settings → Privacy & Security → Developer Mode** and turn it on.
   2. Tap **Restart**.
   3. After the restart, unlock the phone and tap **Turn On**.
   4. Click **Prepare phone** again.

   The first Prepare downloads Apple's developer disk image, so it can take a few minutes.
6. When it says **All set**, you can unplug. Next time it works over Wi-Fi, as long as the phone and computer are on the same network.

You need an iPhone on iOS 17 or later. On iOS 17.0 to 17.3, a Windows PC also needs extra drivers, so update the phone if you can.

## Using it

- **Pick a spot:** search for a place, or click the map to drop a pin.
- **Teleport:** jump straight to the pin.
- **Move here:** travel from your current fake location to the pin at walking, cycling or driving speed. A gradual move looks more natural than a jump.
- **Stop spoofing:** go back to your real location.
- **☆ Save pin:** keep a spot under **Saved places**.
- **Quit:** restore your real location and close the app.

Find My can take a minute or two to update on other people's phones.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No phone found" | Plug in with a cable and unlock the phone. For Wi-Fi, the phone and computer must be on the same network and you must have run Prepare once over cable. |
| "Tap Trust" | Unlock the phone and accept the prompt. If you tapped "Don't Trust", unplug it and plug it back in. |
| Location goes back to real after a while | The app was closed, or the computer went to sleep. Keep it open and stop the computer from sleeping. |
| Prepare fails while mounting the disk image | Check your internet connection, since the image downloads the first time. Then try again with the phone unlocked. |

## Run from source

Requires Python 3.9 or later.

```bash
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m spoofer
```

Built on [pymobiledevice3](https://github.com/doronz88/pymobiledevice3). Map data © OpenStreetMap contributors.
