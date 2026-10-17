"""samples.py - SYNTHETIC data in the same line shapes real devices print.
Invented apps and numbers, safe to publish. Used by --fake mode and by selftest.py."""

BATTERY = """Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  Max charging current: 500000
  status: 2
  health: 2
  present: true
  level: 41
  voltage: 4012
  temperature: 351
  technology: Li-ion
"""

THERMAL = """IsStatusOverride: false
ThermalEventListeners:
Thermal Status: 0
Cached temperatures:
	Temperature{mValue=54.6, mType=0, mName=CPU0, mStatus=0}
	Temperature{mValue=62.4, mType=0, mName=CPU7, mStatus=0}
	Temperature{mValue=84.0, mType=8, mName=socd, mStatus=0}
Current temperatures from HAL:
	Temperature{mValue=37.7, mType=0, mName=CPU0, mStatus=0}
	Temperature{mValue=39.2, mType=0, mName=CPU3, mStatus=0}
	Temperature{mValue=37.6, mType=0, mName=CPU7, mStatus=0}
	Temperature{mValue=35.0, mType=2, mName=battery, mStatus=0}
	Temperature{mValue=37.978, mType=3, mName=skin, mStatus=0}
	Temperature{mValue=36.8, mType=1, mName=GPU0, mStatus=0}
	Temperature{mValue=33.0, mType=8, mName=socd, mStatus=0}
Current cooling devices from HAL:
	CoolingDevice{mValue=0, mType=1, mName=thermal-cpufreq-0}
"""

CPU = """Load: 4.0 / 4.17 / 6.19
CPU usage from 4121ms to 21ms ago (2026-01-02 10:00:01.000 to 2026-01-02 10:00:05.100):
  12% 2440/kworker/0:2-events: 0% user + 12% kernel
  4.5% 29588/kworker/0:3: 0% user + 4.5% kernel
  3.6% 3551/system_server: 1.5% user + 2.1% kernel / faults: 3909 minor 247 major
  2.1% 8120/com.example.chat: 1.4% user + 0.7% kernel
  1% 23956/com.google.android.gms: 0.7% user + 0.2% kernel / faults: 10570 minor 366 major
12% TOTAL: 5% user + 7% kernel + 0% iowait
"""

CRASH = """--------- beginning of crash
12-30 06:03:28.301 19255 19255 F DEBUG   : *** *** *** *** *** *** *** *** *** *** *** *** *** *** *** ***
12-30 06:03:28.301 19255 19255 F DEBUG   : Build fingerprint: 'Vendor/Model/Device:16/BUILD:user/release-keys'
12-30 06:03:28.302 19255 19255 F DEBUG   : pid: 19200, tid: 19250, name: Thread-5  >>> com.example.game <<<
01-02 09:58:11.100  8120  8120 E AndroidRuntime: FATAL EXCEPTION: main
01-02 09:58:11.101  8120  8120 E AndroidRuntime: Process: com.example.chat, PID: 8120
01-02 09:58:11.102  8120  8120 E AndroidRuntime: java.lang.NullPointerException
"""

ERRORS = "\n".join(
    ["01-02 09:59:%02d.568  1906  2080 E ChargerInfo: SingleHyperPath not found" % i for i in range(10, 20)] +
    ["01-02 09:59:%02d.100  1896 11944 E PerformanceService: storage_feature: open failed" % i for i in range(20, 24)] +
    ["01-02 09:59:30.400  8079  8603 E Bluetooth: [gatt_main.cc:1096] gatt_data_process",
     "01-02 09:59:40.000  1000  2000 E ActivityManager: ANR in com.example.chat (com.example.chat/.Main)"])

NPROC = "8\n"

PACKAGES = """package:com.example.video uid:10050
package:com.example.video.overlay uid:10050
package:com.example.chat uid:10051
package:com.example.game uid:10052
package:com.example.music uid:10053
package:com.google.android.gsf uid:10099
package:com.google.android.gms uid:10099
package:android uid:1000
"""


def batterystats():
    L = ["Battery History (50% used, 1234 used of 2048KB, 99 strings using 10KB):",
         "Discharge step durations:",
         "  #0: +30m0s to 99 (screen-on, power-save-off, device-idle-off)",
         "  Time on battery: 1h 0m 0s 0ms (50.0%) realtime, 1h 0m 0s 0ms (50.0%) uptime"]  # decoy before the real section
    L += [f"  filler history line {i}" for i in range(400)]
    L += ["Statistics since last charge:",
          "  System starts: 0, currently on battery: true",
          "  Time on battery: 10h 5m 0s 0ms (80.0%) realtime, 6h 0m 0s 0ms (79.0%) uptime",
          "  Time on battery screen off: 6h 0m 0s 0ms (59.5%) realtime, 2h 0m 0s 0ms (33.0%) uptime",
          "  Time on battery screen doze: 3h 0m 0s 0ms (29.7%)",
          "  Total run time: 12h 36m 0s 0ms realtime, 7h 30m 0s 0ms uptime",
          "  Start clock time: 2026-01-02-08-00-00"]
    L += [f"  filler stats line {i}" for i in range(60)]
    L += ["  Estimated power use (mAh):",
          "    Capacity: 5000, Computed drain: 600, actual drain: 600",
          "    Global",
          "    screen: 500 apps: 500",
          "    cpu: 2000 apps: 1990 duration: 9h 0m 0s 0ms",
          "    bluetooth: 200 apps: 190",
          "    mobile_radio: 300 apps: 300",
          "    wakelock: 10 apps: 10 duration: 3h 0m 0s 0ms",
          "      (on battery, screen on)",
          "      screen: 500 apps: 500",
          "      cpu: 1000 apps: 995 duration: 4h 0m 0s 0ms",
          "  UID 0: 500 bg: 500",
          "  UID 1000: 600 bg: 600",
          "  UID u0a50: 700 fg: 300 (1h 10m 0s 0ms) bg: 90 (45m 0s 100ms) fgs: 0.0100 cached: 50.0 (1h 0m 0s 0ms)",
          "  UID 1002: 200 bg: 200",
          "  UID u0a53: 400 fg: 100 (20m 0s 0ms) bg: 10 (15m 0s 0ms) fgs: 250 (3h 20m 0s 0ms)",
          "  UID u0a51: 300 fg: 40 (10m 0s 0ms) bg: 80 (2h 0m 0s 0ms) cached: 10.0 (1h 0m 0s 0ms)",
          "  UID u0a99: 150 fg: 0.286 bg: 39.5 (47m 0s 0ms) fgs: 113 (2h 40m 0s 0ms)",
          "  UID u0a52: 100 fg: 90 (30m 0s 0ms) bg: 1.0 (1s 0ms)",
          "  UID u0a60: 20 fg: 10 bg: 5 (1m 0s 0ms)",
          "  UID 1082: 30 bg: 30"]
    L += [f"  filler power line {i}" for i in range(50)]
    L += ["  All kernel wake locks:",
          "  Kernel Wake lock PowerManagerService.WakeLocks: 1h 50m 47s 477ms (2 times) realtime",
          "",
          "  All partial wake locks:",
          "  Wake lock u0a53 AudioMix: 2h 10m 0s 0ms (3 times) max=100 actual=200 realtime",
          "  Wake lock u0a50 Playback:Session: 22m 35s 577ms (9 times) max=1947417 actual=3028265 realtime",
          "  Wake lock u0a51 *job*r/@SyncManager@com.android.contacts/com.example.chat:android: 2m 32s 476ms (9 times) max=120100",
          "  Wake lock 1000 NetworkStats: 1m 1s 0ms (5 times) max=1 actual=2 realtime",
          "  Wake lock u0a52 ProximitySensor: 8m 0s 0ms (0 times) max=1 actual=2 realtime",
          "  Wake lock u0a99 IntentOp:.locationreporting.LocationReportUploadIntentOperation: 1m 27s 901ms (55 times) max=23833 actual=116841 realtime",
          "",
          "  Some later section:"]
    return "\n".join(L) + "\n"