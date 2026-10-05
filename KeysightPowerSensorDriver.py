"""
instruments
Author:  Keysight Technologies
Builds instrument specific classes for each power sensor.
The classes include measurement setup/fetch power measurement.
Tested on U2067XA (U/L2050/60 X-Series USB/LAN wide dynamic range power sensors).
"""

import logging
import os
import sys
from typing import Any
from time import sleep
from collections import namedtuple

import numpy as np
import pyvisa

import error

logger = logging.getLogger(__name__)


class PowerSensorBase:
    ps60SeriesFamily = ["U2061XA", "U2062XA", "U2063XA", "U2064XA", "U2065XA", "U2066XA", "U2067XA"]
    ps20SeriesFamily = ["U2021XA", "U2022XA"]
    def __init__(
        self, visaAddress: str, timeout: int = 10, **kwargs: Any
    ) -> None:
        """
        Args:
        visaAddress (string): Instrument host IP address. Argument is a string containing a valid IP address.
        apiType (string): Chooses whether to use PyVISA or socketscpi ["pyvisa", "socketscpi"].
        timeout (int): Timeout in seconds.

        Keyword Args:
        protocol (string): LAN protocol used to communicate with the instrument ("vxi11", "hislip", "socket"). Note this is only usable with PyVISA.
        port (int): Port used by the instrument to facilitate communication (socket default is 5025, vxi11 and hislip defaults are 0).
        """

        # Default connection params; overridden by recognized keyword arguments.
        try:
            self.instance = pyvisa.ResourceManager().open_resource(
                f"{visaAddress}"
            )
        except Exception as e:
            raise error.InstrumentError(f"Failed to connect to power sensor at {visaAddress}: {e}")
        self.instance.timeout = timeout * 1000
        self.instance.write_termination = "\n"
        self.instance.read_termination = "\n"
        self.idString = self.instance.query("*idn?")
        self.instId = self.idString.split(",")[1]
        if self.instId in self.ps60SeriesFamily:
            self.psGroup = "ps60"
        elif self.instId in self.ps20SeriesFamily:
            self.psGroup = "ps20"
        else:
            raise error.InstrumentError("Unknown instrument")

    def __getattr__(self, __name: str) -> Any:
        """This is a passthrough method that allows the base class to access attributes from the parent class.
        See the accepted answer at
        https://stackoverflow.com/questions/65754399/conditional-inheritance-based-on-arguments-in-python
        """
        # Guard against infinite recursion if self.instance was never set
        # (e.g. the connection raised during __init__): accessing self.instance
        # would otherwise call __getattr__("instance") forever.
        if __name == "instance":
            raise AttributeError(__name)
        return self.instance.__getattribute__(__name)

    def err_check(self) -> None:
        """Prints out all errors and clears error queue. Raises InstrumentError with the info of the error encountered."""

        err = []
        cmd = "SYST:ERR?"

        # Cap iterations so a stuck/garbage error queue can't loop forever
        maxErrors = 100

        # SYST:ERR? returns '<code>,"<message>"'; code 0 means the queue is empty.
        # Parse the numeric code rather than string-matching so the sign of real
        # (negative) SCPI error codes is preserved when we log/raise them.
        temp = self.query(cmd).strip()
        while int(temp.split(",")[0]) != 0:
            logger.error("instrument error: %s", temp)
            err.append(temp)
            if len(err) >= maxErrors:
                err.append(f"err_check aborted after {maxErrors} errors without clearing the queue")
                break
            temp = self.query(cmd).strip()
        if err:
            raise error.InstrumentError(err)

    def wait_for_opc(self) -> None:
        """Blocks until all pending instrument operations are complete."""

        self.query("*OPC?")

    def preset(self) -> None:
        """
        Presets the instrument to its default state and waits for completion.
        Note: class attributes queried at connect time.
        """
        self.write("*CLS")
        self.write("*RST")
        self.wait_for_opc()
        self.err_check()

    def print_capabilities(self) -> None:
        """Prints instrument identification."""

        logger.info("Instrument ID: %s", self.idString.strip())

# noinspection PyUnresolvedReferences
class PowerSensor(PowerSensorBase):
    def __init__(
        self,
        visaAddress: str,
        timeout: int = 10,
        reset: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Generic class for controlling the X series
        family power sensor.

        Attributes:
            cf (float): Sets the power sensor's frequency.

        TODO
            Add check to ensure that the correct instrument is connected
        """

        super().__init__(visaAddress, timeout=timeout, **kwargs)
        if reset:
            self.write("*rst")
            self.query("*opc?")

    def configure(self, **kwargs: Any) -> None:
        """
        Sets basic configuration for the power sensor and populates class attributes
        accordingly. Only the keyword arguments listed below are accepted; any
        other key raises KeyError.
        Keyword Arguments:
            cf (float): Sets the power sensor's frequency.
        """

        # Check to see which keyword arguments the user sent and call the appropriate function
        for key, value in kwargs.items():

            if key == "cf":
                self.set_cf(value)
            else:
                raise KeyError(
                    f'Invalid keyword argument: "{key}"'
                )  # raise KeyError('Invalid keyword argument.')

        self.err_check()

    def _read_binary(self, cmd: str) -> list[float]:
        """
        Reads a binary block of 32-bit floats through either API gate.
        Sets the wire format explicitly on every call so the result does not
        depend on byte-order state left behind by other measurements.

        Args:
        cmd (str): SCPI query that returns a binary block (e.g. 'FETCH:OFDM1?').

        Returns:
        list of floats.
        """

        self.write("format:data real,32")
        # "border swap" puts the least-significant byte first (little-endian on
        # X-Series), so both gates must read little-endian to match.
        self.write("format:border swap")
        if self.apiType == "pyvisa":
            return self.instance.query_binary_values(cmd, datatype="f", is_big_endian=False)
        elif self.apiType == "socketscpi":
            return self.instance.query_binary_values(cmd, datatype="f")
        else:
            raise error.InstrumentError(
                f'"{self.apiType}" is not a valid apiType for binary reads.'
            )

    def sanity_check(self) -> None:
        """Logs user-accessible class attributes."""
        logger.info("Center Frequency: %s", self.cf)

    # Standard Power Sensor functions
    def calibrate(self) -> None:
        """
        Calibrates the power sensor.
        """

        self.write("CAL:ALL")
        self.write("CAL:ALL?")
        sleep(10)  # wait 10 seconds for calibration to complete
        cal_result = int(self.read())
        
        self.err_check()
        return cal_result

    def set_frequency_MHz(self, frequency_MHz: float) -> None:
        """
        Sets and reads the frequency of the sensor using SCPI commands.
        Args:
            frequency_MHz (float): Sets the sensor's frequency (MHz).
        """

        if isinstance(frequency_MHz, bool) or not isinstance(frequency_MHz, (int, float)) or frequency_MHz <= 0:
            raise ValueError("Carrier frequency must be a positive number.")
        cf = float(frequency_MHz)

        self.write(f":SENS:FREQ {frequency_MHz}MHz")
        self.write(f"SENS:AVER:STAT OFF")
        self.write(f"CAL:ZERO:AUTO OFF")
        # self.write(f"SENS:MRAT FAST") # use this command can not turn on trace
        
        self.err_check()

    def set_Continuous_measurement(self, Continuous: bool) -> None:
        """
        Configures the power sensor for continuous measurement.
        """
        if Continuous:
            self.write(":INITiate:CONTinuous ON")
        else:
            self.write(":INITiate:CONTinuous OFF")

    def set_fast_measurement(self, fast: bool) -> None:
        """
        Configures the power sensor for fast measurement.
        """
        if fast:
            self.write(":SENS:MRAT FAST")
        else:
            self.write(":SENS:MRAT NORMal")
        
        self.err_check()

    def set_measurement_mode(self, mode: str = "Average") -> None:
        """
        Sets the sensor's measurement mode.
        Args:
            mode (str): Measurement mode ("Average", "Normal").
        """
        if(mode.lower() == "average"):
            self.write(f":SENS:DETector:FUNC AVERage")
        elif(mode.lower() == "normal"):
            self.write(f":SENS:DETector:FUNC NORMal")
        else:
            raise ValueError("Invalid measurement mode.")

        self.err_check()
        
    def set_trigger(self, trigger_source: str, hold_off_s: float = 0.00001, delay_s: float = 0, level_dBm: float = -45) -> None:
        """
        Sets the sensor's trigger setting.
        Args:
            trigger_source (str): Trigger Source ("External", "Immediate". "Internal", "Internal1").
            hold_off_s (float): Trigger hold off time in second.
            delay_s (float): Trigger delay time in second.
            level_dBm (float): Trigger level in dBm for RFB/VID, V for EXT1/EXT2.
        """
        if(trigger_source.lower() == "external"):
            self.write(f"TRIG1:SOUR EXT")
        elif(trigger_source.lower() == "immediate"):
            self.write(f"TRIG1:SOUR IMMediate")
        elif(trigger_source.lower() == "internal"):
            self.write(f"TRIG1:SOUR INTernal")
        elif(trigger_source.lower() == "internal1"):
            self.write(f"TRIG1:SOUR INTernal1")
        else:
            raise ValueError("Invalid Trigger Source.")
        
        if(trigger_source.lower() != "immediate"):
            self.write(f"TRIG1:Hold {hold_off_s}")
            self.write(f"TRIG1:DEL {delay_s}")
            self.write(f"TRIG1:LEV {level_dBm}")

    def set_triggerCount(self, trigger_count: int) -> None:
        """
        Sets the sensor's trigger count.
        COUNt can be set to a value >1 only when [:SENSe[1]:]MRATe is set to FAST.
        Args:
            trigger_count (int): Number of triggers to count 1 to 200.
        """
        self.write(f"TRIG1:COUNT {trigger_count}")
        self.err_check()

    def set_Gate(self, gate_number: int, start_time_s: float, length_s: float) -> None:
        """
        Sets the sensor's gate setting.
        Args:
            gate_number (int): gate number (1, 2, 3, 4).
            start_time_s (float): gate start time in second.
            length_s (float): gate length in second.
        """
        self.write(f":SENS:SWE{gate_number}:OFFSet:TIME {length_s}")
        self.write(f":SENS:SWE{gate_number}:TIME {start_time_s}")

        self.err_check()

    def set_Trace(self, trace_unit: str, start_time_s: float, duration_s: float) -> None:
        """
        Sets the sensor's gate setting.
        Args:
            trace_unit (str): trace unit {Watt, dBm}.
            start_time_s (float): trace start time in second.
            duration_s (float): trace duration in second.
        """
        if(self.query("TRIG1:SOUR?") == "INT" or self.query("TRIG1:SOUR?") == "EXT"):
            self.write("TRAC1:STAT ON")
            if(trace_unit.lower() == "watt"):
                self.write("TRAC1:UNIT W")
            elif(trace_unit.lower() == "dbm"):
                self.write("TRAC1:UNIT DBM")
            else:
                raise ValueError("Invalid trace unit.")
            self.write(f"SENS1:TRACe:OFFSet:TIME {start_time_s}")
            self.write(f"SENS1:TRACe:TIME {duration_s}")
        
        self.err_check()

    def query_power(self) -> None:
        """
        Triggers a single sweep and returns the power results.
        Returns:
            average power
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")

        data = self.query(":FETCh?").strip().split(",")
        length = len(data)
        if length < 1:
            raise error.InstrumentError("No data returned from power sensor.")
        elif length == 1:
            avg_power = float(data[0])
        else:
            value = [float(i) for i in data]
            avg_power = np.mean(value)
            # avg_power = float(data[0])

        self.err_check()

        return avg_power


