/*
 * ESP32 LED Strip Controller
 *
 * Connects to the same WiFi as the ESP32-CAM (e.g. stefanIot).
 * Wiring: 5V and GND to strip power; LED_PIN to strip data line.
 *
 * Strip type (set below):
 *   USE_WS2812 = 1  -> WS2812B/NeoPixel addressable strip (most common "1-wire" strips)
 *   USE_WS2812 = 0  -> Simple digital strip (HIGH = on, LOW = off)
 *
 * For WS2812: install "Adafruit NeoPixel" in Arduino Library Manager.
 * Set NUM_LEDS to match your strip length.
 *
 * HTTP: GET /on, GET /off, GET /fun?p=50&h=180, GET /
 *   /fun: p = position 0-100, h = hue 0-360 (cluster of LEDs at position with color)
 * Serial (115200): send "on", "off", "status"
 */

#include <WiFi.h>
#include <WebServer.h>
#include "secrets.h"

#define LED_PIN 4       // IO4 - data pin
#define USE_WS2812 1    // 1 = WS2812B/NeoPixel, 0 = simple digital on/off
#define NUM_LEDS 64     // Total LEDs (e.g. 8 sections = 8, or 8×8 = 64 if 8 LEDs per section)
#define LED_BRIGHTNESS 100  // 0-255. Lower = less current; can reduce white→red gradient at strip end (voltage drop)
#define CLUSTER_SIZE 8     // Number of LEDs lit in "fun" mode (moving cluster)

#if USE_WS2812
#include <Adafruit_NeoPixel.h>
Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);
#endif

WebServer server(80);
bool lightsOn = false;

static void setLights(bool on) {
  lightsOn = on;
#if USE_WS2812
  if (on) {
    strip.fill(strip.Color(LED_BRIGHTNESS, LED_BRIGHTNESS, LED_BRIGHTNESS));  // dimmer white = less voltage drop
  } else {
    strip.fill(0);
  }
  strip.show();
  Serial.printf("[LED] %s -> %d LEDs %s\n", on ? "ON " : "OFF", NUM_LEDS, on ? "on" : "off");
#else
  digitalWrite(LED_PIN, on ? HIGH : LOW);
  Serial.printf("[LED] %s -> GPIO%d = %s\n", on ? "ON " : "OFF", LED_PIN, on ? "HIGH" : "LOW");
#endif
}

void handleOn() {
  setLights(true);
  server.send(200, "text/plain", "OK on");
}

void handleOff() {
  setLights(false);
  server.send(200, "text/plain", "OK off");
}

#if USE_WS2812
// Hue 0-360 -> R,G,B (S=255, V=255), scaled by LED_BRIGHTNESS. NEO_GRB order.
static uint32_t hueToColor(uint16_t hue) {
  hue = hue % 360;
  uint8_t r = 0, g = 0, b = 0;
  const uint8_t c = 255;
  uint8_t d = hue % 60;
  uint8_t x = (uint8_t)((uint16_t)c * (60 - abs((int)(2 * d) - 60)) / 60);
  if (hue < 60)       { r = c; g = x; b = 0; }
  else if (hue < 120) { r = x; g = c; b = 0; }
  else if (hue < 180) { r = 0; g = c; b = x; }
  else if (hue < 240) { r = 0; g = x; b = c; }
  else if (hue < 300) { r = x; g = 0; b = c; }
  else                { r = c; g = 0; b = x; }
  r = (uint16_t)r * LED_BRIGHTNESS / 255;
  g = (uint16_t)g * LED_BRIGHTNESS / 255;
  b = (uint16_t)b * LED_BRIGHTNESS / 255;
  return strip.Color(g, r, b);  // NEO_GRB
}

void handleFun() {
  int p = server.arg("p").toInt();
  int h = server.arg("h").toInt();
  p = (p < 0) ? 0 : (p > 100) ? 100 : p;
  h = (h < 0) ? 0 : (h > 360) ? 360 : h;
  strip.fill(0);
  float centerF = (p / 100.0f) * (NUM_LEDS - 1);
  int center = (int)(centerF + 0.5f);
  int half = CLUSTER_SIZE / 2;
  int startIdx = center - half;
  int endIdx = center + half;
  if (startIdx < 0) startIdx = 0;
  if (endIdx >= NUM_LEDS) endIdx = NUM_LEDS - 1;
  uint32_t color = hueToColor((uint16_t)h);
  for (int i = startIdx; i <= endIdx; i++) {
    strip.setPixelColor(i, color);
  }
  strip.show();
  server.send(200, "text/plain", "OK fun");
}
#endif

void handleRoot() {
  server.send(200, "text/plain", lightsOn ? "on" : "off");
}

void processSerialCommand(const String& cmd) {
  String c = cmd;
  c.trim();
  c.toLowerCase();
  if (c == "on") {
    setLights(true);
    Serial.println("-> OK, lights ON");
  } else if (c == "off") {
    setLights(false);
    Serial.println("-> OK, lights OFF");
  } else if (c == "status" || c == "?" || c.length() == 0) {
#if USE_WS2812
    Serial.printf("-> status: %s (%d LEDs)\n", lightsOn ? "ON" : "OFF", NUM_LEDS);
#else
    Serial.printf("-> status: %s, GPIO%d = %s\n",
                  lightsOn ? "ON" : "OFF", LED_PIN,
                  digitalRead(LED_PIN) ? "HIGH" : "LOW");
#endif
  } else if (c.length() > 0) {
    Serial.println("-> unknown (use: on, off, status)");
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n--- ESP32 LED Strip (IO4) ---");
#if USE_WS2812
  strip.begin();
  strip.show();  // start all off
  Serial.printf("WS2812: %d LEDs on GPIO%d\n", NUM_LEDS, LED_PIN);
#else
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.printf("Simple strip: GPIO%d OUTPUT, LOW\n", LED_PIN);
#endif

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.printf("LED strip controller: http://%s\n", WiFi.localIP().toString().c_str());
  Serial.println("Serial: send 'on' or 'off' to toggle. 'status' for state.");

  server.on("/on", HTTP_GET, handleOn);
  server.on("/off", HTTP_GET, handleOff);
#if USE_WS2812
  server.on("/fun", HTTP_GET, handleFun);
#endif
  server.on("/", HTTP_GET, handleRoot);
  server.begin();
}

void loop() {
  server.handleClient();

  // Serial debug: line-based commands
  static String line;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (line.length() > 0) {
        processSerialCommand(line);
        line = "";
      }
    } else {
      line += c;
    }
  }
}
