/*
 * ESP32-CAM Minimal Fast Stream
 *
 * Stripped-down firmware for maximum JPEG throughput.
 * Endpoints:
 *   /cam.jpg   – single JPEG snapshot (640x480)
 *   /stream    – MJPEG stream (640x480)
 *   /          – bare-bones HTML viewer
 *
 * Hardware: ESP32-CAM (AI Thinker, OV2640)
 */

#include <WebServer.h>
#include <WiFi.h>
#include <esp32cam.h>
#include "secrets.h"

WebServer server(80);

static auto res = esp32cam::Resolution::find(1280, 720);

static void handleStream() {
  WiFiClient client = server.client();
  client.setNoDelay(true);
  client.print("HTTP/1.1 200 OK\r\n"
               "Content-Type: multipart/x-mixed-replace;boundary=f\r\n\r\n");

  while (client.connected()) {
    auto frame = esp32cam::capture();
    if (frame == nullptr) break;

    client.printf("--f\r\nContent-Type:image/jpeg\r\nContent-Length:%u\r\n\r\n",
                  frame->size());
    frame->writeTo(client);
    client.print("\r\n");
  }
}

static const char htmlPage[] PROGMEM =
  "<title>CAM</title><img src=/stream style=width:100%>";

void setup() {
  Serial.begin(115200);
  Serial.println("\nESP32-CAM init");

  {
    using namespace esp32cam;
    Config cfg;
    cfg.setPins(pins::AiThinker);
    cfg.setResolution(res);
    cfg.setBufferCount(2);
    cfg.setJpeg(50);

    if (!Camera.begin(cfg)) {
      Serial.println("CAM FAIL");
      delay(3000);
      ESP.restart();
    }
    // Flip image upside down (e.g. if camera is mounted inverted)
    Camera.update([](esp32cam::Settings& s) { s.vflip = true; });
  }

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setTxPower(WIFI_POWER_17dBm);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nhttp://%s\n", WiFi.localIP().toString().c_str());

  server.on("/", HTTP_GET, []() {
    server.send_P(200, "text/html", htmlPage);
  });
  server.on("/stream", HTTP_GET, handleStream);

  server.begin();
}

void loop() {
  server.handleClient();
}
