// Servo test bench: XIAO RP2040 + PCA9685 (SDA D4, SCL D5, addr 0x40, 50 Hz), servos on CH0-CH3.
// Needs library "Adafruit PWM Servo Driver Library". Board: Seeed XIAO RP2040.
// Flashing this replaces CircuitPython; re-flash the CircuitPython UF2 afterwards.
//
// Boots into a slow 1000-2000 us sweep on all four channels. Serial Monitor @115200, newline:
//   1500        all channels to 1500 us
//   2 1800      channel 2 to 1800 us
//   s           sweep again
//   r           relax (PWM off)
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>

Adafruit_PWMServoDriver pca(0x40);
const int NUM_CH = 4, US_MIN = 500, US_MAX = 2500;
const int SWEEP_LO = 1000, SWEEP_HI = 2000, SWEEP_STEP = 10;
bool sweeping = true;
int sweepUs = 1500, sweepDir = 1;

void setUs(int ch, int us) {
  us = constrain(us, US_MIN, US_MAX);
  pca.setPWM(ch, 0, (uint16_t)((long)us * 4096L / 20000L));  // 20 ms period at 50 Hz
  Serial.print("ch"); Serial.print(ch); Serial.print(" = "); Serial.println(us);
}

void setAll(int us) { for (int ch = 0; ch < NUM_CH; ch++) setUs(ch, us); }

void relax() { for (int ch = 0; ch < 16; ch++) pca.setPWM(ch, 0, 0); Serial.println("relaxed"); }

void setup() {
  Serial.begin(115200);
  Wire.begin();
  pca.begin();
  pca.setOscillatorFrequency(25000000);
  pca.setPWMFreq(50);
  delay(10);
  setAll(1500);
  delay(1000);
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line == "s") { sweeping = true; Serial.println("sweep"); }
    else if (line == "r") { sweeping = false; relax(); }
    else if (line.length()) {
      sweeping = false;
      int sp = line.indexOf(' ');
      if (sp < 0) setAll(line.toInt());
      else {
        int ch = line.substring(0, sp).toInt();
        if (ch >= 0 && ch < 16) setUs(ch, line.substring(sp + 1).toInt());
      }
    }
  }
  if (sweeping) {
    sweepUs += sweepDir * SWEEP_STEP;
    if (sweepUs >= SWEEP_HI || sweepUs <= SWEEP_LO) sweepDir = -sweepDir;
    for (int ch = 0; ch < NUM_CH; ch++)
      pca.setPWM(ch, 0, (uint16_t)((long)sweepUs * 4096L / 20000L));
    delay(20);
  }
}
