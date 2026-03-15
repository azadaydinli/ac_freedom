"""Constants for AUX Cloud API device parameters."""

# ── AC parameter keys ──────────────────────────────────────────────
AUX_MODE = "ac_mode"
AUX_ECOMODE = "ecomode"
AUX_ERROR_FLAG = "err_flag"

AC_POWER = "pwr"
AC_TEMPERATURE_TARGET = "temp"       # value ×10, e.g. 240 = 24.0 °C
AC_TEMPERATURE_AMBIENT = "envtemp"   # value ×10
AC_FAN_SPEED = "ac_mark"
AC_MODE_SPECIAL = "mode"

AC_SWING_VERTICAL = "ac_vdir"
AC_SWING_HORIZONTAL = "ac_hdir"

AC_AUXILIARY_HEAT = "ac_astheat"
AC_CLEAN = "ac_clean"
AC_HEALTH = "ac_health"
AC_CHILD_LOCK = "childlock"
AC_COMFORTABLE_WIND = "comfwind"
AC_MILDEW_PROOF = "mldprf"
AC_SLEEP = "ac_slp"
AC_SCREEN_DISPLAY = "scrdisp"
AC_POWER_LIMIT = "pwrlimit"
AC_POWER_LIMIT_SWITCH = "pwrlimitswitch"


# ── Fan speed enum ──────────────────────────────────────────────────
class ACFanSpeed:
    PARAM_NAME = "ac_mark"
    AUTO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    TURBO = 4
    MUTE = 5


# ── Product ID → device type mapping ───────────────────────────────
class AuxProducts:
    class DeviceType:
        AC_GENERIC = [
            "000000000000000000000000c0620000",
            "0000000000000000000000002a4e0000",
        ]
        HEAT_PUMP = ["000000000000000000000000c3aa0000"]

    # Parameters to query for AC
    AC_PARAMS = [
        AC_AUXILIARY_HEAT, AC_CLEAN, AC_SWING_HORIZONTAL, AC_HEALTH,
        AC_FAN_SPEED, AUX_MODE, AC_SLEEP, AC_SWING_VERTICAL,
        AUX_ECOMODE, AUX_ERROR_FLAG, AC_MILDEW_PROOF, AC_POWER,
        AC_SCREEN_DISPLAY, AC_TEMPERATURE_TARGET, AC_TEMPERATURE_AMBIENT,
        AC_POWER_LIMIT, AC_POWER_LIMIT_SWITCH, AC_CHILD_LOCK,
        AC_COMFORTABLE_WIND, "new_type", "ac_tempconvert", "sleepdiy",
        "ac_errcode1", "tempunit", "tenelec",
    ]

    AC_SPECIAL_PARAMS = [AC_MODE_SPECIAL]

    @staticmethod
    def get_params_list(product_id: str) -> list[str] | None:
        if product_id in AuxProducts.DeviceType.AC_GENERIC:
            return AuxProducts.AC_PARAMS
        return None

    @staticmethod
    def get_special_params_list(product_id: str) -> list[str] | None:
        if product_id in AuxProducts.DeviceType.AC_GENERIC:
            return AuxProducts.AC_SPECIAL_PARAMS
        return None

    @staticmethod
    def get_device_name(product_id: str) -> str:
        if product_id in AuxProducts.DeviceType.AC_GENERIC:
            return "AUX Air Conditioner"
        return "AUX Device"
