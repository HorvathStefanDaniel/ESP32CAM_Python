/*
 * ESP32-CAM Web Stream (esp32cam library)
 *
 * Uses the esp32cam library (https://github.com/yoursunny/esp32cam) for
 * fast snapshot serving. One JPEG per request; best FPS with /cam-lo.jpg
 * or /snap-lo.jpg (Python scripts default to snapshot polling).
 *
 * Hardware: ESP32-CAM-MB with ESP32-CAM (OV2640)
 * Install: ESP32 Arduino core, esp32cam library, then select AI Thinker ESP32-CAM.
 */

#include <WebServer.h>
#include <WiFi.h>
#include <esp32cam.h>
#include "secrets.h"

WebServer server(80);

// Resolutions: smaller = higher FPS
static auto loRes = esp32cam::Resolution::find(320, 240);   // best FPS
static auto midRes = esp32cam::Resolution::find(400, 296);   // SVGA-ish
static auto hiRes = esp32cam::Resolution::find(800, 600);   // higher quality

static void serveJpg() {
  auto frame = esp32cam::capture();
  if (frame == nullptr) {
    server.send(503, "", "");
    return;
  }
  server.setContentLength(frame->size());
  server.send(200, "image/jpeg");
  WiFiClient client = server.client();
  frame->writeTo(client);
}

static void handleJpgLo() {
  if (!esp32cam::Camera.changeResolution(loRes)) {
    Serial.println("SET-LO-RES FAIL");
  }
  serveJpg();
}

static void handleJpgMid() {
  if (!esp32cam::Camera.changeResolution(midRes)) {
    Serial.println("SET-MID-RES FAIL");
  }
  serveJpg();
}

static void handleJpgHi() {
  if (!esp32cam::Camera.changeResolution(hiRes)) {
    Serial.println("SET-HI-RES FAIL");
  }
  serveJpg();
}

// MJPEG stream: throttle by timestamp (~30 FPS). Send raw multipart (no chunked
// encoding) so Python/browsers get a continuous stream without getting stuck.
#define STREAM_FPS 30
#define STREAM_INTERVAL_MS (1000 / STREAM_FPS)

static void handleStream() {
  if (!esp32cam::Camera.changeResolution(loRes)) {
    Serial.println("stream: SET-LO-RES FAIL");
  }
  WiFiClient client = server.client();
  client.print("HTTP/1.1 200 OK\r\n"
               "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n"
               "Connection: keep-alive\r\n"
               "\r\n");
  client.flush();
  unsigned long lastFrameTime = 0;
  while (client.connected()) {
    unsigned long now = millis();
    if (now - lastFrameTime >= STREAM_INTERVAL_MS) {
      auto frame = esp32cam::capture();
      if (frame == nullptr) break;
      client.print("--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ");
      client.print(frame->size());
      client.print("\r\n\r\n");
      client.flush();  // push part header so client can parse Content-Length before JPEG
      // send JPEG in small chunks so first frame is delivered (writeTo can block on large buffer)
      const size_t chunkSize = 1024;
      const uint8_t* p = frame->data();
      for (size_t i = 0; i < frame->size(); i += chunkSize) {
        size_t n = (frame->size() - i) > chunkSize ? chunkSize : (frame->size() - i);
        client.write(p + i, n);
      }
      client.print("\r\n");
      client.flush();
      lastFrameTime = now;
    } else {
      delay(1);
    }
  }
}

// HTML: live view by polling /cam-lo.jpg (no Serial in hot path)
static const char htmlPage[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ESP32-CAM Stream</title>
  <style>
    body { font-family: Arial, sans-serif; text-align: center; background-color: #1a1a1a; color: #fff; margin: 0; padding: 20px; }
    h1 { color: #4CAF50; }
    img { border: 2px solid #4CAF50; border-radius: 10px; max-width: 100%; height: auto; }
    .status { margin: 20px 0; padding: 10px; background-color: #2a2a2a; border-radius: 5px; }
  </style>
</head>
<body>
  <h1>ESP32-CAM Live Stream</h1>
  <div class="status">Status: <span id="status">Connecting...</span></div>
  <img src="/stream" alt="Camera" id="stream">
  <script>
    var img = document.getElementById('stream');
    var status = document.getElementById('status');
    img.onload = function() { status.textContent = 'Connected'; status.style.color = '#4CAF50'; };
    img.onerror = function() { status.textContent = 'Error'; status.style.color = '#f44336'; };
  </script>
</body>
</html>
)rawliteral";

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("=================================");
  Serial.println("ESP32-CAM (esp32cam library)");
  Serial.println("=================================");

  {
    using namespace esp32cam;
    Config cfg;
    cfg.setPins(pins::AiThinker);
    cfg.setResolution(loRes);  // default = best FPS
    cfg.setBufferCount(2);
    cfg.setJpeg(80);

    bool ok = Camera.begin(cfg);
    if (!ok) {
      Serial.println("CAMERA FAIL");
      delay(5000);
      ESP.restart();
    }
    Serial.println("CAMERA OK");
  }

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi FAIL");
    delay(5000);
    ESP.restart();
  }
  Serial.println("WiFi OK");
  Serial.print("http://");
  Serial.println(WiFi.localIP());
  Serial.println("  /cam-lo.jpg  /cam-mid.jpg  /cam-hi.jpg");
  Serial.println("  /snap-lo.jpg (alias)  /snap.jpg (alias)");
  Serial.println("  /stream (MJPEG, one client at a time)");
  Serial.println("=================================");

  server.on("/", HTTP_GET, []() {
    server.setContentLength(sizeof(htmlPage) - 1);
    server.send(200, "text/html");
    server.sendContent_P(htmlPage, sizeof(htmlPage) - 1);
  });
  server.on("/cam-lo.jpg", HTTP_GET, handleJpgLo);
  server.on("/cam-mid.jpg", HTTP_GET, handleJpgMid);
  server.on("/cam-hi.jpg", HTTP_GET, handleJpgHi);
  server.on("/snap-lo.jpg", HTTP_GET, handleJpgLo);
  server.on("/snap.jpg", HTTP_GET, handleJpgMid);
  server.on("/stream", HTTP_GET, handleStream);

  server.begin();
  Serial.println("Web server started");
}

void loop() {
  server.handleClient();
}
