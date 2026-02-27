/*
 * ESP32 LED Strip Controller
 *
 * Connects to the same WiFi as the ESP32-CAM.
 * Wiring: 5V and GND to strip power; LED_PIN to strip data line.
 *
 * Strip type (set below):
 *   USE_WS2812 = 1  -> WS2812B/NeoPixel addressable strip (most common "1-wire" strips)  <- required for the more advanced effects
 *   USE_WS2812 = 0  -> Simple digital strip (HIGH = on, LOW = off)
 *
 * For WS2812: install "Adafruit NeoPixel" in Arduino Library Manager.
 * Set NUM_LEDS to match your strip length.
 *
 * HTTP: GET /on, GET /off, GET /fun?p=50&h=180, GET /explosion?p=50&h=180, GET /
 *   /fun: p = position 0-100, h = hue 0-360 (cluster); optional p2, h2 for second hand
 *   /explosion: p,h = start; 3 pixels move to each end and stack there (Tetris-style). Ignore repeat pinch until done; 2 hands OK.
 *   /rainbow or /groovy: whole strip rainbow effect (persistent mode like on/off).
 *   /mode?m=on|off|rainbow|strobe|breathe: set whole strip to one mode.
 *   /split?left=L&right=R: left/right half modes (L,R = on, off, rainbow, strobe, breathe).
 *   /ripple?h=H or /ripple?p=P&h=H&dir=D&v=V: position-driven 1D ripples (PEACE). p=0-100, dir=±1, v=0-50 (reach).
 * Serial (115200): send "on", "off", "status"
 */

#include <WiFi.h>
#include <WebServer.h>
#include <string.h>
#include "secrets.h"

#define LED_PIN 14       // I14 - data pin
#define USE_WS2812 1    // 1 = WS2812B/NeoPixel, 0 = simple digital on/off
#define NUM_LEDS 300     // Total LEDs (e.g. 8 sections = 8, or 8×8 = 64 if 8 LEDs per section)
#define LED_BRIGHTNESS 120   // 0-255. Initial brightness (runtime adjustable via thumbs up/down)
#define MIN_BRIGHTNESS 20    // Never go below this when decreasing (thumbs down)
#define BRIGHTNESS_STEP 10   // Step when increasing or decreasing

#define CLUSTER_SIZE 8       // Number of LEDs lit in "fun" mode (moving cluster)
#define EXPLOSION_SIZE 3    // Number of LEDs in explosion burst (travels from spot)
#define EXPLOSION_FPS 30    // Target frame rate for explosion animation

#if USE_WS2812
#include <Adafruit_NeoPixel.h>
Adafruit_NeoPixel strip(NUM_LEDS, LED_PIN, NEO_GRB + NEO_KHZ800);
uint8_t currentBrightness = LED_BRIGHTNESS;  // Runtime brightness (thumbs up/down)

// Persistent fun state (so we can redraw base + explosion every frame)
uint8_t funNumIslands = 0;   // 0 = solid, 1 or 2 = islands
int funPos[2] = { 0, 0 };   // position 0-100 per island
uint16_t funHue[2] = { 0, 0 };

// Strip mode: 0=off, 1=on (solid), 2=rainbow, 3=strobe, 4=breathe. When splitActive, left half = leftMode, right half = rightMode.
#define MODE_OFF 0
#define MODE_ON  1
#define MODE_RAINBOW 2
#define MODE_STROBE  3
#define MODE_BREATHE 4
uint8_t leftMode = MODE_OFF;
uint8_t rightMode = MODE_OFF;
bool splitActive = false;
uint16_t rainbowOffset = 0;  // advances each frame for flowing rainbow
unsigned long lastRainbowMs = 0;
#define RAINBOW_MS_PER_TICK 50
bool strobePhase = false;    // for MODE_STROBE: alternate on/off
unsigned long lastStrobeMs = 0;
#define STROBE_MS 80
uint16_t breathePhase = 0;   // 0..359 for sine; breath brightness
unsigned long lastBreatheMs = 0;
#define BREATHE_MS_PER_TICK 25
// Ripple: 1D water-style ripples from hand position (PEACE gesture). Strip is solid; ripples move and dissipate.
#define RIPPLE_MAX 12
#define RIPPLE_SPEED 2.0f
#define RIPPLE_DECAY 0.94f       // amplitude decay per frame
#define RIPPLE_BASE_REACH 12.0f // minimum half-width in LEDs
#define RIPPLE_VELOCITY_REACH 1.2f  // extra reach per velocity unit from Python (v 0-50)
#define RIPPLE_MS_PER_FRAME 25
struct Ripple {
  float pos;
  int dir;
  uint16_t hue;
  float amplitude;
  float reach;
  bool active;
};
Ripple rippleList[RIPPLE_MAX];
int rippleCount = 0;
unsigned long lastRippleMs = 0;

// Explosion: 3 pixels move to each end and stack there (Tetris-style). One pinch per hand until done.
bool explosionActive[2] = { false, false };
int explosionCenter[2] = { 0, 0 };
uint16_t explosionHue[2] = { 0, 0 };
bool explosionLeftActive[2] = { false, false };
int explosionLeftPos[2] = { 0, 0 };
bool explosionRightActive[2] = { false, false };
int explosionRightPos[2] = { 0, 0 };
uint32_t stackedColor[NUM_LEDS];  // stacked pixels at ends (left then right)
int leftStackCount = 0;   // number of 3-pixel groups stacked at start of strip
int rightStackCount = 0;  // number of 3-pixel groups stacked at end of strip
unsigned long lastDrawMs = 0;
#endif

WebServer server(80);
bool lightsOn = false;

#if USE_WS2812
// Clear ripples, explosions, stacked pixels, fun islands - same as "off". Call when turning off.
static void clearAllEffects() {
  leftMode = MODE_OFF;
  rightMode = MODE_OFF;
  splitActive = false;
  funNumIslands = 0;
  rippleCount = 0;
  for (int i = 0; i < RIPPLE_MAX; i++) rippleList[i].active = false;
  explosionActive[0] = explosionActive[1] = false;
  explosionLeftActive[0] = explosionLeftActive[1] = false;
  explosionRightActive[0] = explosionRightActive[1] = false;
  leftStackCount = 0;
  rightStackCount = 0;
  memset(stackedColor, 0, sizeof(stackedColor));
}
#endif

static void setLights(bool on) {
  lightsOn = on;
#if USE_WS2812
  if (!on) {
    clearAllEffects();
  } else {
    leftMode = MODE_ON;
    rightMode = MODE_ON;
    splitActive = false;
  }
  drawAll();
  Serial.printf("[LED] %s -> %d LEDs %s\n", on ? "ON " : "OFF", NUM_LEDS, on ? "on" : "off");
#else
  digitalWrite(LED_PIN, on ? HIGH : LOW);
  Serial.printf("[LED] %s -> GPIO%d = %s\n", on ? "ON " : "OFF", LED_PIN, on ? "HIGH" : "LOW");
#endif
}

void handleOn() {
#if USE_WS2812
  funNumIslands = 0;
  explosionActive[0] = explosionActive[1] = false;
  explosionLeftActive[0] = explosionLeftActive[1] = false;
  explosionRightActive[0] = explosionRightActive[1] = false;
  leftStackCount = 0;
  rightStackCount = 0;
  memset(stackedColor, 0, sizeof(stackedColor));
  leftMode = MODE_ON;
  rightMode = MODE_ON;
  splitActive = false;
  lightsOn = true;
  drawAll();
#endif
  server.send(200, "text/plain", "OK on");
}

void handleOff() {
  setLights(false);
  server.send(200, "text/plain", "OK off");
}

#if USE_WS2812
// Draw rainbow in range [start, start+count), hue offset by rainbowOffset
static void drawRainbowRange(int start, int count) {
  for (int i = 0; i < count && (start + i) < NUM_LEDS; i++) {
    uint16_t hue = ((unsigned long)(start + i) * 360UL / (NUM_LEDS > 0 ? NUM_LEDS : 1) + rainbowOffset) % 360;
    strip.setPixelColor(start + i, hueToColor(hue));
  }
}

// Strobe: fill range with color or off depending on phase (fast blink)
static void drawStrobeRange(int start, int count, bool phaseOn) {
  uint32_t c = phaseOn ? hueToColor(rainbowOffset % 360) : 0;
  for (int i = 0; i < count && (start + i) < NUM_LEDS; i++) {
    strip.setPixelColor(start + i, c);
  }
}

// Breathe: fill range with white at pulsed brightness (sine wave 0.25..1.0)
static void drawBreatheRange(int start, int count) {
  // breathePhase 0..359 -> factor ~0.25 to 1.0
  float rad = (float)breathePhase * 2.0f * 3.14159265f / 360.0f;
  float factor = 0.25f + 0.75f * (sin(rad) + 1.0f) * 0.5f;
  uint8_t b = (uint8_t)((float)currentBrightness * factor);
  uint32_t c = strip.Color(b, b, b);
  for (int i = 0; i < count && (start + i) < NUM_LEDS; i++) {
    strip.setPixelColor(start + i, c);
  }
}

static void parseModeArg(const String& arg, uint8_t* out) {
  String a = arg;
  a.trim();
  a.toLowerCase();
  if (a == "off") *out = MODE_OFF;
  else if (a == "rainbow" || a == "groovy") *out = MODE_RAINBOW;
  else if (a == "strobe" || a == "party") *out = MODE_STROBE;
  else if (a == "breathe") *out = MODE_BREATHE;
  else *out = MODE_ON;  // "on" or anything else
}

void handleRainbow() {
  leftMode = MODE_RAINBOW;
  rightMode = MODE_RAINBOW;
  splitActive = false;
  funNumIslands = 0;
  lightsOn = true;
  drawAll();
  server.send(200, "text/plain", "OK rainbow");
}

void handleMode() {
  String m = server.arg("m");
  parseModeArg(m, &leftMode);
  rightMode = leftMode;
  splitActive = false;
  funNumIslands = 0;
  lightsOn = (leftMode != MODE_OFF);
#if USE_WS2812
  if (leftMode == MODE_OFF) clearAllEffects();  // same as /off: clear ripples, explosions, etc.
#endif
  drawAll();
  server.send(200, "text/plain", "OK mode");
}

void handleSplit() {
  String l = server.arg("left");
  String r = server.arg("right");
  parseModeArg(l, &leftMode);
  parseModeArg(r, &rightMode);
  splitActive = true;
  funNumIslands = 0;
  lightsOn = (leftMode != MODE_OFF || rightMode != MODE_OFF);
  drawAll();
  server.send(200, "text/plain", "OK split");
}

static int addRipple(int positionPct, uint16_t hue, int direction, int velocity) {
  if (rippleCount >= RIPPLE_MAX) {
    // Remove oldest inactive or first slot
    for (int i = 0; i < RIPPLE_MAX; i++) {
      if (!rippleList[i].active) {
        rippleCount--;
        for (int j = i; j < RIPPLE_MAX - 1; j++) rippleList[j] = rippleList[j + 1];
        rippleList[RIPPLE_MAX - 1].active = false;
        break;
      }
    }
    if (rippleCount >= RIPPLE_MAX) {
      rippleList[0].active = false;
      for (int i = 0; i < RIPPLE_MAX - 1; i++) rippleList[i] = rippleList[i + 1];
      rippleCount = RIPPLE_MAX - 1;
    }
  }
  float pos = (positionPct / 100.0f) * (float)(NUM_LEDS - 1);
  float reach = RIPPLE_BASE_REACH + (float)(velocity > 50 ? 50 : velocity) * RIPPLE_VELOCITY_REACH;
  if (reach > 60.0f) reach = 60.0f;
  int idx = rippleCount;
  rippleList[idx].pos = pos;
  rippleList[idx].dir = (direction >= 0) ? 1 : -1;
  rippleList[idx].hue = hue;
  rippleList[idx].amplitude = 1.0f;
  rippleList[idx].reach = reach;
  rippleList[idx].active = true;
  rippleCount++;
  return idx;
}

void handleRipple() {
  int h = server.arg("h").toInt();
  h = (h < 0) ? 0 : (h > 360) ? 360 : h;
  uint16_t hue = (uint16_t)h;
  String pArg = server.arg("p");
  if (pArg.length() > 0) {
    int p = pArg.toInt();
    p = (p < 0) ? 0 : (p > 100) ? 100 : p;
    int dir = server.arg("dir").toInt();
    if (dir >= 0) dir = 1; else dir = -1;
    int v = server.arg("v").toInt();
    if (v < 0) v = 0; else if (v > 50) v = 50;
    lightsOn = true;
    leftMode = MODE_OFF;
    rightMode = MODE_OFF;
    splitActive = false;
    funNumIslands = 0;
    addRipple(p, hue, dir, v);
  } else {
    // Backwards compat: add one ripple at center
    lightsOn = true;
    leftMode = MODE_OFF;
    rightMode = MODE_OFF;
    splitActive = false;
    funNumIslands = 0;
    addRipple(50, hue, 1, 10);
  }
  drawAll();
  server.send(200, "text/plain", "OK ripple");
}
#endif

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
  r = (uint16_t)r * currentBrightness / 255;
  g = (uint16_t)g * currentBrightness / 255;
  b = (uint16_t)b * currentBrightness / 255;
  return strip.Color(g, r, b);  // NEO_GRB
}

// Blend two NeoPixel colors (NEO_GRB); w = 0..255 = weight of second color
static uint32_t blendColor(uint32_t c1, uint32_t c2, uint8_t w) {
  uint8_t g1 = (c1 >> 16) & 0xFF, r1 = (c1 >> 8) & 0xFF, b1 = c1 & 0xFF;
  uint8_t g2 = (c2 >> 16) & 0xFF, r2 = (c2 >> 8) & 0xFF, b2 = c2 & 0xFF;
  uint8_t g = (uint16_t)g1 * (255 - w) / 255 + (uint16_t)g2 * w / 255;
  uint8_t r = (uint16_t)r1 * (255 - w) / 255 + (uint16_t)r2 * w / 255;
  uint8_t b = (uint16_t)b1 * (255 - w) / 255 + (uint16_t)b2 * w / 255;
  return strip.Color(g, r, b);
}

static void drawCluster(int center, uint32_t color) {
  int half = CLUSTER_SIZE / 2;
  int startIdx = center - half;
  int endIdx = center + half;
  if (startIdx < 0) startIdx = 0;
  if (endIdx >= NUM_LEDS) endIdx = NUM_LEDS - 1;
  for (int i = startIdx; i <= endIdx; i++) {
    strip.setPixelColor(i, color);
  }
}

static void drawAll() {
  strip.fill(0);
  // When ripples are active, skip base drawing so only the ripple effect is visible on a dark strip
  if (lightsOn && rippleCount == 0) {
    int mid = NUM_LEDS / 2;
    uint32_t white = strip.Color(currentBrightness, currentBrightness, currentBrightness);
    if (splitActive) {
      if (leftMode == MODE_RAINBOW) drawRainbowRange(0, mid);
      else if (leftMode == MODE_ON) for (int i = 0; i < mid; i++) strip.setPixelColor(i, white);
      else if (leftMode == MODE_STROBE) drawStrobeRange(0, mid, strobePhase);
      else if (leftMode == MODE_BREATHE) drawBreatheRange(0, mid);
      if (rightMode == MODE_RAINBOW) drawRainbowRange(mid, NUM_LEDS - mid);
      else if (rightMode == MODE_ON) for (int i = mid; i < NUM_LEDS; i++) strip.setPixelColor(i, white);
      else if (rightMode == MODE_STROBE) drawStrobeRange(mid, NUM_LEDS - mid, strobePhase);
      else if (rightMode == MODE_BREATHE) drawBreatheRange(mid, NUM_LEDS - mid);
    } else {
      if (leftMode == MODE_RAINBOW) {
        drawRainbowRange(0, NUM_LEDS);
      } else if (leftMode == MODE_STROBE) {
        drawStrobeRange(0, NUM_LEDS, strobePhase);
      } else if (leftMode == MODE_BREATHE) {
        drawBreatheRange(0, NUM_LEDS);
      } else if (leftMode == MODE_ON) {
        if (funNumIslands > 0) {
          for (uint8_t i = 0; i < funNumIslands; i++) {
            int center = (int)((funPos[i] / 100.0f) * (NUM_LEDS - 1) + 0.5f);
            drawCluster(center, hueToColor(funHue[i]));
          }
        } else {
          strip.fill(white);
        }
      }
    }
  }
  // Ripple overlay: 1D water ripples on dark strip (only ripples visible when rippleCount > 0)
  for (int i = 0; i < rippleCount; i++) {
    if (!rippleList[i].active) continue;
    Ripple* r = &rippleList[i];
    uint32_t rippleColor = hueToColor(r->hue);
    int startLed = (int)(r->pos - r->reach);
    int endLed = (int)(r->pos + r->reach + 1.0f);
    if (startLed < 0) startLed = 0;
    if (endLed > NUM_LEDS) endLed = NUM_LEDS;
    for (int idx = startLed; idx < endLed; idx++) {
      float dist = fabsf((float)idx - r->pos);
      if (dist >= r->reach) continue;
      float t = 1.0f - dist / r->reach;
      uint8_t blendW = (uint8_t)((float)255 * t * r->amplitude * 0.85f);
      if (blendW < 2) continue;
      uint32_t cur = strip.getPixelColor(idx);
      strip.setPixelColor(idx, blendColor(cur, rippleColor, blendW));
    }
  }
  // Stacked pixels at ends (Tetris-style: left stack then right stack)
  for (int i = 0; i < NUM_LEDS; i++) {
    if (stackedColor[i] != 0) strip.setPixelColor(i, stackedColor[i]);
  }
  // Moving 3-pixel bursts (no trail; they stack when they reach the end)
  int half = EXPLOSION_SIZE / 2;
  for (int hand = 0; hand < 2; hand++) {
    uint32_t c = hueToColor(explosionHue[hand]);
    if (explosionLeftActive[hand]) {
      for (int d = -half; d <= half; d++) {
        int idx = explosionLeftPos[hand] + d;
        if (idx >= 0 && idx < NUM_LEDS) strip.setPixelColor(idx, c);
      }
    }
    if (explosionRightActive[hand]) {
      for (int d = -half; d <= half; d++) {
        int idx = explosionRightPos[hand] + d;
        if (idx >= 0 && idx < NUM_LEDS) strip.setPixelColor(idx, c);
      }
    }
  }
  strip.show();
}

static void updateExplosion() {
  for (int hand = 0; hand < 2; hand++) {
    uint32_t c = hueToColor(explosionHue[hand]);
    if (explosionLeftActive[hand]) {
      explosionLeftPos[hand]--;
      // Stop and stack when block reaches the existing left stack (not necessarily pixel 0)
      int leftStackEdge = leftStackCount * 3 + 1;  // center of next 3-pixel slot
      if (explosionLeftPos[hand] <= leftStackEdge) {
        int base = leftStackCount * 3;
        if (base + 3 <= NUM_LEDS - rightStackCount * 3) {
          for (int i = 0; i < 3; i++) stackedColor[base + i] = c;
          leftStackCount++;
        }
        explosionLeftActive[hand] = false;
      }
    }
    if (explosionRightActive[hand]) {
      explosionRightPos[hand]++;
      // Stop and stack when block reaches the existing right stack (not necessarily strip end)
      int rightStackBase = NUM_LEDS - (rightStackCount + 1) * 3;
      int rightStackCenter = rightStackBase + 1;
      if (explosionRightPos[hand] >= rightStackCenter) {
        if (rightStackBase >= leftStackCount * 3) {
          for (int i = 0; i < 3; i++) stackedColor[rightStackBase + i] = c;
          rightStackCount++;
        }
        explosionRightActive[hand] = false;
      }
    }
    explosionActive[hand] = explosionLeftActive[hand] || explosionRightActive[hand];
  }
}

void handleFun() {
  int p = server.arg("p").toInt();
  int h = server.arg("h").toInt();
  p = (p < 0) ? 0 : (p > 100) ? 100 : p;
  h = (h < 0) ? 0 : (h > 360) ? 360 : h;
  lightsOn = true;
  funNumIslands = 1;
  funPos[0] = p;
  funHue[0] = (uint16_t)h;
  String p2Arg = server.arg("p2");
  String h2Arg = server.arg("h2");
  if (p2Arg.length() > 0 && h2Arg.length() > 0) {
    int p2 = p2Arg.toInt();
    int h2 = h2Arg.toInt();
    p2 = (p2 < 0) ? 0 : (p2 > 100) ? 100 : p2;
    h2 = (h2 < 0) ? 0 : (h2 > 360) ? 360 : h2;
    funNumIslands = 2;
    funPos[1] = p2;
    funHue[1] = (uint16_t)h2;
  }
  drawAll();
  server.send(200, "text/plain", "OK fun");
}

static void startExplosion(int hand, int centerPx, uint16_t hue) {
  explosionCenter[hand] = centerPx;
  explosionHue[hand] = hue;
  explosionLeftPos[hand] = centerPx;
  explosionLeftActive[hand] = true;
  explosionRightPos[hand] = centerPx;
  explosionRightActive[hand] = true;
  explosionActive[hand] = true;
}

void handleExplosion() {
  // ?p=50&h=180 (hand 0) | ?p2=80&h2=270 (hand 1 only) | ?p1=50&h1=180&p2=80&h2=270 (both)
  String p1Arg = server.arg("p1");
  String h1Arg = server.arg("h1");
  String p2Arg = server.arg("p2");
  String h2Arg = server.arg("h2");
  if (p1Arg.length() > 0 && h1Arg.length() > 0) {
    int p1 = p1Arg.toInt();
    int h1 = h1Arg.toInt();
    p1 = (p1 < 0) ? 0 : (p1 > 100) ? 100 : p1;
    h1 = (h1 < 0) ? 0 : (h1 > 360) ? 360 : h1;
    int c1 = (int)((p1 / 100.0f) * (NUM_LEDS - 1) + 0.5f);
    startExplosion(0, c1, (uint16_t)h1);
    if (p2Arg.length() > 0 && h2Arg.length() > 0) {
      int p2 = p2Arg.toInt();
      int h2 = h2Arg.toInt();
      p2 = (p2 < 0) ? 0 : (p2 > 100) ? 100 : p2;
      h2 = (h2 < 0) ? 0 : (h2 > 360) ? 360 : h2;
      int c2 = (int)((p2 / 100.0f) * (NUM_LEDS - 1) + 0.5f);
      startExplosion(1, c2, (uint16_t)h2);
    }
  } else if (p2Arg.length() > 0 && h2Arg.length() > 0) {
    int p2 = p2Arg.toInt();
    int h2 = h2Arg.toInt();
    p2 = (p2 < 0) ? 0 : (p2 > 100) ? 100 : p2;
    h2 = (h2 < 0) ? 0 : (h2 > 360) ? 360 : h2;
    int c2 = (int)((p2 / 100.0f) * (NUM_LEDS - 1) + 0.5f);
    startExplosion(1, c2, (uint16_t)h2);
  } else {
    int p = server.arg("p").toInt();
    int h = server.arg("h").toInt();
    p = (p < 0) ? 0 : (p > 100) ? 100 : p;
    h = (h < 0) ? 0 : (h > 360) ? 360 : h;
    int c = (int)((p / 100.0f) * (NUM_LEDS - 1) + 0.5f);
    startExplosion(0, c, (uint16_t)h);
  }
  drawAll();
  server.send(200, "text/plain", "OK explosion");
}

void handleBrightnessUp() {
  if (currentBrightness < 255) {
    currentBrightness = (255 - currentBrightness <= BRIGHTNESS_STEP) ? 255 : (currentBrightness + BRIGHTNESS_STEP);
    if (lightsOn) drawAll();
  }
  server.send(200, "text/plain", String("OK brightness ") + currentBrightness);
}

void handleBrightnessDown() {
  if (currentBrightness > MIN_BRIGHTNESS) {
    currentBrightness = (currentBrightness - BRIGHTNESS_STEP < MIN_BRIGHTNESS) ? MIN_BRIGHTNESS : (currentBrightness - BRIGHTNESS_STEP);
    if (lightsOn) drawAll();
  }
  server.send(200, "text/plain", String("OK brightness ") + currentBrightness);
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
  } else if (c == "bright+" || c == "brightness+") {
#if USE_WS2812
    if (currentBrightness < 255) {
      currentBrightness = (255 - currentBrightness <= BRIGHTNESS_STEP) ? 255 : (currentBrightness + BRIGHTNESS_STEP);
      if (lightsOn) drawAll();
    }
    Serial.printf("-> brightness %d\n", currentBrightness);
#endif
  } else if (c == "bright-" || c == "brightness-") {
#if USE_WS2812
    if (currentBrightness > MIN_BRIGHTNESS) {
      currentBrightness = (currentBrightness - BRIGHTNESS_STEP < MIN_BRIGHTNESS) ? MIN_BRIGHTNESS : (currentBrightness - BRIGHTNESS_STEP);
      if (lightsOn) drawAll();
    }
    Serial.printf("-> brightness %d\n", currentBrightness);
#endif
  } else if (c == "status" || c == "?" || c.length() == 0) {
#if USE_WS2812
    Serial.printf("-> status: %s (%d LEDs), brightness %d\n", lightsOn ? "ON" : "OFF", NUM_LEDS, currentBrightness);
#else
    Serial.printf("-> status: %s, GPIO%d = %s\n",
                  lightsOn ? "ON" : "OFF", LED_PIN,
                  digitalRead(LED_PIN) ? "HIGH" : "LOW");
#endif
  } else if (c.length() > 0) {
    Serial.println("-> unknown (use: on, off, bright+, bright-, status)");
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
  server.on("/rainbow", HTTP_GET, handleRainbow);
  server.on("/groovy", HTTP_GET, handleRainbow);
  server.on("/mode", HTTP_GET, handleMode);
  server.on("/split", HTTP_GET, handleSplit);
  server.on("/ripple", HTTP_GET, handleRipple);
  server.on("/fun", HTTP_GET, handleFun);
  server.on("/explosion", HTTP_GET, handleExplosion);
  server.on("/brightness/up", HTTP_GET, handleBrightnessUp);
  server.on("/brightness/down", HTTP_GET, handleBrightnessDown);
#endif
  server.on("/", HTTP_GET, handleRoot);
  server.begin();
}

void loop() {
  server.handleClient();

#if USE_WS2812
  unsigned long now = millis();
  bool needRedraw = false;
  if (explosionActive[0] || explosionActive[1]) {
    if (now - lastDrawMs >= (1000 / EXPLOSION_FPS)) {
      updateExplosion();
      needRedraw = true;
      lastDrawMs = now;
    }
  }
  // Advance rainbow animation when any half is in rainbow mode
  if (leftMode == MODE_RAINBOW || rightMode == MODE_RAINBOW) {
    if (now - lastRainbowMs >= RAINBOW_MS_PER_TICK) {
      rainbowOffset = (rainbowOffset + 2) % 360;
      lastRainbowMs = now;
      needRedraw = true;
    }
  }
  // Strobe: toggle phase
  if (leftMode == MODE_STROBE || rightMode == MODE_STROBE) {
    if (now - lastStrobeMs >= STROBE_MS) {
      strobePhase = !strobePhase;
      lastStrobeMs = now;
      needRedraw = true;
    }
  }
  // Breathe: advance phase
  if (leftMode == MODE_BREATHE || rightMode == MODE_BREATHE) {
    if (now - lastBreatheMs >= BREATHE_MS_PER_TICK) {
      breathePhase = (breathePhase + 2) % 360;
      lastBreatheMs = now;
      needRedraw = true;
    }
  }
  // Ripple: advance all ripples (move and decay), remove when faded
  if (rippleCount > 0 && now - lastRippleMs >= RIPPLE_MS_PER_FRAME) {
    lastRippleMs = now;
    for (int i = 0; i < rippleCount; i++) {
      if (!rippleList[i].active) continue;
      rippleList[i].pos += (float)rippleList[i].dir * RIPPLE_SPEED;
      rippleList[i].amplitude *= RIPPLE_DECAY;
      if (rippleList[i].amplitude < 0.01f) {
        rippleList[i].active = false;
      }
    }
    // Compact: remove inactive from list
    int w = 0;
    for (int r = 0; r < rippleCount; r++) {
      if (rippleList[r].active) {
        if (w != r) rippleList[w] = rippleList[r];
        w++;
      }
    }
    rippleCount = w;
    needRedraw = (rippleCount > 0);
  }
  if (needRedraw) drawAll();
#endif

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
