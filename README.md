# ESP32-CAM Simple Web Stream Project

A beginner-friendly ESP32-CAM project that streams live video to your web browser. Perfect for learning how to use the ESP32-CAM module and verifying your hardware works correctly.

## Hardware Requirements

- **ESP32-CAM Module**: Camera module with ESP32-S chip
- **ESP32-CAM-MB**: Base board with USB port for programming
- USB cable (USB-A to USB-C or micro-USB, depending on your base board)
- Computer with Arduino IDE installed

## Hardware Setup

1. **Connect the modules**: Slot the ESP32-CAM module into the ESP32-CAM-MB base board
2. **Connect USB**: Plug the USB cable from the base board to your computer
3. **Power**: Ensure the board is properly powered (USB should provide power)

## Software Setup

### 1. Install Arduino IDE

If you don't have Arduino IDE installed:
- Download from: https://www.arduino.cc/en/software
- Install the IDE following the installation instructions for your operating system

### 2. Install ESP32 Board Support

1. Open Arduino IDE
2. Go to **File > Preferences**
3. In the "Additional Board Manager URLs" field, add:
   ```
   https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
   ```
   (If you already have URLs there, add a comma and then paste the URL)
4. Click **OK**
5. Go to **Tools > Board > Boards Manager**
6. Search for **"ESP32"**
7. Find **"esp32 by Espressif Systems"** and click **Install**
8. Wait for installation to complete (this may take a few minutes)

### 3. Install Required Libraries

1. Go to **Tools > Manage Libraries**
2. Search for **"esp32cam"** (by Junxiao Shi / yoursunny) and install it
3. The ESP32 board package includes WiFi; esp32cam wraps the camera driver

**Note**: This project uses the [esp32cam](https://github.com/yoursunny/esp32cam) library. Select **AI Thinker ESP32-CAM** in the Board menu to enable PSRAM.

### 4. Configure Arduino IDE Settings

1. **Select Board**:
   - Go to **Tools > Board > ESP32 Arduino**
   - Select **"AI Thinker ESP32-CAM"**

2. **Select Port**:
   - Go to **Tools > Port**
   - Select the COM port where your ESP32-CAM-MB is connected
   - On Windows, it will show as "COM3", "COM4", etc.
   - On Mac/Linux, it will show as "/dev/cu.usbserial-..." or "/dev/ttyUSB..."

3. **Set Upload Speed**:
   - Go to **Tools > Upload Speed**
   - Select **115200**

4. **Other Recommended Settings**:
   - **CPU Frequency**: 240MHz (WiFi/BT)
   - **Flash Frequency**: 80MHz
   - **Flash Size**: 4MB (32Mb)
   - **Partition Scheme**: Default 4MB with spiffs (1.2MB APP/1.5MB SPIFFS)
   - **Core Debug Level**: None (or Info for more debugging)

## Project Setup

### 1. Clone or Download This Project

Download all files from this project to a folder on your computer.

### 2. Configure WiFi Credentials

1. **Create `secrets.h` file**:
   - Copy `secrets_example.h` and rename it to `secrets.h`
   - Or create a new file named `secrets.h` in the same folder as `Test.ino`

2. **Edit `secrets.h`**:
   - Open `secrets.h` in a text editor or Arduino IDE
   - Replace `YOUR_WIFI_SSID` with your actual WiFi network name (SSID)
   - Replace `YOUR_WIFI_PASSWORD` with your actual WiFi password
   - Save the file

   Example:
   ```cpp
   const char* ssid = "MyWiFiNetwork";
   const char* password = "MyPassword123";
   ```

**Important**: 
- Never commit `secrets.h` to version control (it's already in `.gitignore` if using git)
- Only commit `secrets_example.h` as a template

### 3. Open the Project

1. Open Arduino IDE
2. Go to **File > Open**
3. Navigate to the project folder
4. Open `Test.ino`

## Uploading and Running

### 1. Upload the Code

1. Make sure your ESP32-CAM-MB is connected via USB
2. Verify the correct board and port are selected (see Software Setup section)
3. Click the **Upload** button (arrow icon) in Arduino IDE
4. Wait for compilation and upload to complete
   - You may see "Connecting..." messages - this is normal
   - The upload process may take 30-60 seconds

### 2. Monitor Serial Output

1. After upload completes, open **Tools > Serial Monitor**
2. Set baud rate to **115200** (bottom right of Serial Monitor)
3. You should see output like:
   ```
   =================================
   ESP32-CAM Web Stream Starting...
   =================================
   Initializing camera...
   Camera initialized successfully!
   
   Connecting to WiFi: YourWiFiName
   ......
   WiFi connected successfully!
   IP Address: http://192.168.1.100
   ```

### 3. Access the Web Stream

1. **Note the IP Address**: Look for the IP address in the Serial Monitor output
2. **Open Web Browser**: Open any web browser (Chrome, Firefox, Safari, etc.)
3. **Enter URL**: Type the IP address shown in Serial Monitor
   - Example: `http://192.168.1.100`
4. **View Stream**: You should see a web page with live video from your ESP32-CAM!

## Troubleshooting

### Camera Initialization Failed

**Error**: "Camera init failed with error 0x..."

**Solutions**:
- Verify the camera module is properly seated in the base board
- Check that you selected "AI Thinker ESP32-CAM" board
- Ensure adequate power supply (try a different USB cable or port)
- Check Serial Monitor for specific error codes

### WiFi Connection Failed

**Error**: "WiFi connection failed!"

**Solutions**:
- Verify WiFi SSID and password in `secrets.h` are correct
- Check that your WiFi network is 2.4GHz (ESP32 doesn't support 5GHz)
- Ensure WiFi network is in range
- Check if your router blocks new devices (MAC filtering)
- Try restarting the ESP32-CAM

### Upload Failed

**Error**: "Failed to connect to ESP32"

**Solutions**:
- Hold the BOOT button on the ESP32-CAM-MB while clicking Upload
- Try a different USB cable (some cables are power-only)
- Check that the correct COM port is selected
- Lower the upload speed to 921600 or 460800
- Try pressing the RESET button on the board

### No Video in Browser

**Symptoms**: Web page loads but no video stream

**Solutions**:
- Check Serial Monitor for error messages
- Verify camera initialized successfully (check Serial Monitor)
- Try refreshing the browser page
- Check browser console for errors (F12)
- Ensure you're accessing the correct IP address
- Try a different browser

### Serial Monitor Shows Garbage

**Symptoms**: Random characters instead of readable text

**Solutions**:
- Verify Serial Monitor baud rate is set to **115200**
- Close and reopen Serial Monitor
- Check that the correct COM port is selected

## Project Structure

```
Test/
├── Test.ino              # Main Arduino sketch
├── secrets.h             # WiFi credentials (create from secrets_example.h)
├── secrets_example.h     # Template for WiFi credentials
└── README.md            # This file
```

## Features

- **Live Video Streaming**: Stream video from ESP32-CAM to web browser
- **MJPEG Format**: Efficient streaming format for web browsers
- **Simple Web Interface**: Clean, modern web page to view the stream
- **Serial Debugging**: Comprehensive Serial Monitor output for troubleshooting
- **Error Handling**: Helpful error messages guide you through issues

## Camera Settings (esp32cam library)

The firmware uses the [esp32cam](https://github.com/yoursunny/esp32cam) library. Default is 320x240 (loRes) for best FPS.

- **Endpoints**: `/cam-lo.jpg` (320x240, best FPS), `/cam-mid.jpg`, `/cam-hi.jpg` (800x600). Aliases: `/snap-lo.jpg` = `/cam-lo.jpg`, `/snap.jpg` = `/cam-mid.jpg`.
- **Format**: JPEG (quality 80 in library terms).
- **Board**: In Arduino IDE, select **AI Thinker ESP32-CAM** and install the **esp32cam** library (e.g. from Library Manager).

## Python computer vision scripts

You can run object counting or face detection on the live stream from your PC. A runner script creates a virtual environment and installs dependencies automatically.

### Automatic (recommended)

From the project folder in PowerShell:

```powershell
.\run.ps1 object_count
.\run.ps1 face_detect
```

- **First run:** Creates a `.venv` folder and installs packages from `requirements.txt`.
- **Later runs:** Reuses `.venv` and ensures dependencies are installed, then runs the script.

Pass the stream URL or other options after the script name (replace `192.168.1.100` with your ESP32-CAM IP from Serial Monitor):

```powershell
.\run.ps1 object_count --url http://192.168.1.100/cam-lo.jpg
.\run.ps1 face_detect --url http://192.168.1.100/cam-lo.jpg --recognize
```

**Best FPS:** The scripts default to **snapshot polling** at `http://192.168.1.100/cam-lo.jpg` (320x240, one JPEG per request). Override with `--url`:

- **`/cam-lo.jpg`** – 320x240, best FPS. Default for Python scripts. (Alias: `/snap-lo.jpg`.)
- **`/cam-mid.jpg`** – Mid resolution. (Alias: `/snap.jpg`.)
- **`/cam-hi.jpg`** – 800x600, higher quality.

If PowerShell blocks the script, run once: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

### Manual

Create a venv and install dependencies yourself, then run:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python object_count.py --url http://YOUR_ESP32_IP/cam-lo.jpg
python face_detect.py --url http://YOUR_ESP32_IP/cam-lo.jpg
```

Replace `YOUR_ESP32_IP` with the IP shown in the Arduino Serial Monitor. Use `/cam-lo.jpg` for best FPS, or `/cam-mid.jpg` / `/cam-hi.jpg` for higher resolution.

## Next Steps

Once you have the basic stream working, you can:
- Adjust camera resolution and quality settings
- Add motion detection
- Save photos to SD card
- Add OTA (Over-The-Air) updates
- Create a mobile app interface
- Add authentication to the web interface

## Resources

- [ESP32 Arduino Documentation](https://docs.espressif.com/projects/arduino-esp32/en/latest/)
- [ESP32-CAM Pinout Reference](https://randomnerdtutorials.com/esp32-cam-pinout-reference/)
- [Arduino IDE Documentation](https://www.arduino.cc/en/software)

## License

This project is provided as-is for educational purposes.

## Support

If you encounter issues:
1. Check the Troubleshooting section above
2. Review Serial Monitor output for error messages
3. Verify all setup steps were completed correctly
4. Check that your hardware is properly connected

---

**Happy Streaming!** 🎥
