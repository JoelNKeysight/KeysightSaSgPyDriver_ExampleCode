"""
IMPORTANT: This Software includes one or more computer programs bearing a Keysight copyright notice and in source code format (“Source Files”), such Source Files are 
subject to the terms and conditions of the Keysight Software End-User License Agreement (“EULA”) www.Keysight.com/find/sweula and these Supplemental Terms. 
BY USING THE SOURCE FILES, YOU AGREE TO BE BOUND BY THE TERMS AND CONDITIONS OF THE EULA INCLUDING THESE SUPPLEMENTAL TERMS. 
IF YOU DO NOT AGREE TO THESE TERMS AND CONDITIONS, DO NOT COPY OR DISTRIBUTE THE SOURCE FILES. 
Additional Rights and Limitations. If Source Files are included with the Software, Keysight grants you a limited, non-exclusive license, without a right to sub-license,
to copy, modify and distribute the Source Files solely to develop and distribute a system or product to which you have added value and only if such system or product contains at least one Keysight instrument.
You own any such modifications and Keysight retains all right, title and interest in the underlying Software and Source Files. All rights not expressly granted are reserved by Keysight. 
Distribution Requirements. 
Any distribution of the Source Files, unmodified or modified, to an external party shall be in conjunction with distribution of your system or product and shall be pursuant to an enforceable agreement
that provides similar protections for Keysight and its suppliers as those contained in the EULA and these Supplemental Terms.  

General. Capitalized terms used in these Supplemental Terms and not otherwise defined herein shall have the meanings assigned to them in the EULA. 
To the extent that any of these Supplemental Terms conflict with terms in the EULA, these Supplemental Terms control solely with respect to the Source Files. 
"""


"""
instruments
Author: Keysight Technologies
Builds instrument specific classes for each signal generator.
The classes include minimum waveform length/granularity checks, binary
waveform formatting, sequencer length/granularity checks, sample rate
checks, etc. per instrument.
Tested on M9484C (VXG) and N5186A.
"""

import logging
import os
from typing import Any

import numpy as np
import pyvisa
import socketscpi  # type: ignore[import-untyped]  # socketscpi ships no type stubs

import error

logger = logging.getLogger(__name__)

base_dir = os.getcwd()
local_waveform_path = os.path.join(base_dir, "waveform")
vxg_waveform_path = (
    "D:\\Users\\Instrument\\Documents\\Keysight\\PathWave\\SignalGenerator\\Waveforms"
)


def wraparound_calc(length: int, gran: int, minLen: int) -> int:
    """
    HELPER FUNCTION
    Computes the number of times to repeat a waveform based on
    generator granularity requirements.
    Args:
        length (int): Length of waveform
        gran (int): Granularity of waveform, determined by signal generator class
        minLen: Minimum wfm length, determined by signal generator class

    Returns:
        (int) Number of repeats required to satisfy gran and minLen requirements
    """

    repeats = 1
    temp = length
    # Cap repeats so a degenerate gran/minLen can't loop forever.
    # Granularity is satisfied within `gran` repeats; min length within
    # ceil(minLen/length). Sum is a safe upper bound with margin.
    maxRepeats = gran + (minLen // max(length, 1)) + 1
    while temp % gran != 0 or temp < minLen:
        temp += length
        repeats += 1
        if repeats > maxRepeats:
            raise error.InstrumentError(
                f"wraparound_calc could not satisfy granularity {gran} / minLen {minLen} "
                f"for waveform length {length} within {maxRepeats} repeats."
            )
    if repeats > 1:
        logger.info("Waveform repeated %d times.", repeats)
    return repeats


class SignalGeneratorBase:
    def __init__(
        self, ipAddress: str, apiType: str = "socketscpi", timeout: int = 10, **kwargs: Any
    ) -> None:
        """
        Args:
        ipAddress (string): Instrument host IP address. Argument is a string containing a valid IP address.
        apiType (string): Chooses whether to use PyVISA or socketscpi ["pyvisa", "socketscpi"].
        timeout (int): Timeout in seconds.

        Keyword Args:
        protocol (string): LAN protocol used to communicate with the instrument ("vxi11", "hislip", "socket"). Note this is only usable with PyVISA.
        port (int): Port used by the instrument to facilitate communication (socket default is 5025, vxi11 and hislip defaults are 0).
        """

        self.apiType = apiType

        # Default connection params; overridden by recognized keyword arguments.
        protocol: Any = None
        port: Any = None
        for key, value in kwargs.items():
            if key == "protocol":
                protocol = value
            elif key == "port":
                port = value
            else:
                raise KeyError(f"{key} is not a valid keyword argument.")

        if self.apiType == "socketscpi":
            self.instance = socketscpi.SocketInstrument(
                ipAddress, port=port if port is not None else 5025, timeout=timeout, noDelay=True
            )
        elif self.apiType == "pyvisa":
            if protocol is None:
                raise ValueError(
                    'pyvisa apiType requires a "protocol" keyword (e.g. protocol="hislip").'
                )
            _port = port if port is not None else 0
            if protocol.lower() == "vxi11":
                self.instance = pyvisa.ResourceManager().open_resource(
                    f"tcpip::{ipAddress}::inst{_port}::instr"
                )
            elif protocol.lower() == "hislip":
                self.instance = pyvisa.ResourceManager().open_resource(
                    f"tcpip::{ipAddress}::hislip{_port}::instr"
                )
            elif protocol.lower() == "socket":
                raise error.InstrumentError(
                    'socket protocol in PyVISA is not currently working, use "hislip" or "vxi11"'
                )
            else:
                raise ValueError('Invalid protocol selection. Use "vxi11", "hislip", or "socket".')
            # PyVISA's open_resource() method doesn't have a timeout argument, so use a separate method to set it.
            self.instance.timeout = timeout * 1000
        else:
            raise ValueError(
                f'"{self.apiType}" is not a valid apiType, use "socketscpi" or "pyvisa".'
            )

        self.instId = self.instance.query("*idn?")

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

    def _require_remote_dir(self, remotePath: str) -> None:
        r"""
        Pre-flight check: raise RemotePathError if the folder portion of a
        Windows-style remote path does not exist on the instrument, so a missing
        destination fails with a clear message before any binary transfer.

        Args:
            remotePath (str): Full destination path on the instrument, e.g.
                r"D:\Users\...\Waveforms\foo.wfm".
        """

        remoteDir = remotePath.rsplit("\\", 1)[0]
        # MMEM:CAT? reports "<bytesUsed>,<bytesFree>,..." for a real directory
        # and "0,0,..." for one that does not exist (verified on N9042B/N9032B/
        # M9484C). bytesFree is never 0 on a working drive, so 0/0 means missing.
        fields = self.query(f'MMEM:CAT? "{remoteDir}"').strip().split(",")
        used = fields[0].strip() if fields else "0"
        free = fields[1].strip() if len(fields) > 1 else "0"
        if used == "0" and free == "0":
            # Drain the file-name error the failed catalog query just queued so
            # it can't trip a later err_check(), then raise a clear message.
            self.instance.query("SYST:ERR?")
            raise error.RemotePathError(
                f"Remote folder '{remoteDir}' does not exist on the instrument. "
                "Create it first, or pass an existing remotePath."
            )

    def preset(self) -> None:
        """
        Presets the instrument to its default state and waits for completion.
        Note: class attributes queried at connect time (frequency, power, etc.)
        are not refreshed and may no longer match the instrument state.
        """

        self.write("*RST")
        self.wait_for_opc()
        self.err_check()

    def print_capabilities(self) -> None:
        """Prints instrument identification and installed options."""

        logger.info("Instrument ID: %s", self.instId.strip())
        logger.info("Installed options: %s", self.query("*OPT?").strip())


# noinspection PyAttributeOutsideInit,PyUnresolvedReferences
class VXG(SignalGeneratorBase):
    vxgFamily = ["M9484C", "M9383A","SG6420A"]
    mxgFamily = ["N5186A"]

    # def __init__(self, host, port=5025, timeout=10, reset=False):
    def __init__(
        self,
        ipAddress: str,
        apiType: str = "socketscpi",
        timeout: int = 10,
        reset: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Generic class for controlling the VXG signal generator.

        Attributes:
            rf1State (bool): Turns the RF output on or off. (True, False)
            modState (bool): Turns the baseband modulator on or off. (True, False)
            cf (float): Sets the generator's carrier frequency.
            amp (int/float): Sets the generator's RF output power.
            alcState (bool): Turns the ALC (automatic level control) on or off. (True, False)
            iqScale (int): Scales the IQ modulator. Default/safe value is 70
            refSrc (str): Sets the reference clock source. ('int', 'ext', 'bbg')
            fs (float): Sets the sample rate of the baseband generator.

        TODO
            Add check to ensure that the correct instrument is connected
        """

        # super().__init__(host, port, timeout)
        super().__init__(ipAddress, apiType=apiType, timeout=timeout, **kwargs)
        if reset:
            self.write("*rst")
            self.query("*opc?")

        # Query IDN to identify different command required for different instrument
        idString = self.query("*idn?")
        self.inst_id = idString.split(",")[1]
        if self.inst_id in self.vxgFamily:
            self.sgGroup = "vxg"
        elif self.inst_id in self.mxgFamily:
            self.sgGroup = "mxg"
        else:
            raise error.InstrumentError("Unknown instrument")

        # Count channels using per-channel option queries. *OPT? is unreliable on
        # some firmware versions (response is truncated with "..."), so query each
        # channel slot individually — an empty string means the slot is absent.
        self.numCh = 0
        for _ch in range(1, 3):
            if self.query(f"system:rf{_ch}:opt?").strip() != '""':
                self.numCh += 1

        # Query all settings from VXG and store them as class attributes.
        # Binary states are stored as bool so callers can use them directly in
        # boolean context (a raw "0" string is truthy, which would be wrong).
        self.rfState1 = bool(int(self.query("rf1:output?").strip()))
        self.modState1 = bool(int(self.query("rf1:output:modulation?").strip()))
        self.cf1 = float(self.query("source:rf1:frequency?").strip())
        self.amp1 = float(self.query("rf1:power?").strip())
        self.arbState1 = bool(int(self.query("signal1:state?").strip()))
        self.alcState1 = bool(int(self.query("rf1:power:alc?").strip()))
        self.iqScale1 = float(self.query("source:signal1:waveform:scale?").strip())
        self.rms1 = float(self.query("source:signal1:waveform:rms?").strip())
        self.fs1 = float(self.query("signal1:waveform:sclock:rate?").strip())

        # If there are two channels, repeat the queries above for the second channel
        if self.numCh == 2:
            self.rfState2 = bool(int(self.query("rf2:output?").strip()))
            self.modState2 = bool(int(self.query("rf2:output:modulation?").strip()))
            self.cf2 = float(self.query("source:rf2:frequency?").strip())
            self.amp2 = float(self.query("rf2:power?").strip())
            self.arbState2 = bool(int(self.query("signal2:state?").strip()))
            self.alcState2 = bool(int(self.query("rf2:power:alc?").strip()))
            self.iqScale2 = float(self.query("source:signal2:waveform:scale?").strip())
            self.rms2 = float(self.query("source:signal2:waveform:rms?").strip())
            self.fs2 = float(self.query("signal2:waveform:sclock:rate?").strip())

        # Reference source settings are independent of channel number.
        self.refSrc = self.query("roscillator:source?").strip()

        if "int" in self.refSrc.lower():
            self.refFreq = 10e6
        elif "ext" in self.refSrc.lower():
            self.refFreq = float(self.query("roscillator:frequency:external?").strip())
        else:
            raise error.InstrumentError("Unknown refSrc selected.")

        # Initialize waveform format constants and populate them with check_resolution()
        self.minLen = 512
        self.binMult = 32767
        self.gran = 8

    # def configure(self, rfState=1, modState=1, cf=1e9, amp=-20, alcState=0, iqScale=70, refSrc='int', fs=200e6):
    def configure(self, **kwargs: Any) -> None:
        """
        Sets basic configuration for VXG and populates class attributes accordingly.
        Keyword Arguments:
            rfState1|2 (bool): Turns the RF output on (True) or off (False).
            modState1|2 (bool): Turns the baseband modulator on (True) or off (False).
            arbState1|2 (bool): Turns the arb waveform generator on (True) or off (False).
            cf1|2 (float): Sets the generator's carrier frequency.
            amp1|2 (int/float): Sets the generator's RF output power.
            alcState (bool): Turns the ALC (automatic level control) on (True) or off (False).
            iqScale (int): Scales the IQ modulator. Default/safe value is 70
            refSrc (str): Sets the reference clock source. ('int', 'ext', 'bbg')
            fs (float): Sets the sample rate of the baseband generator.
        """

        # Map each bare setting name to its setter. A key may carry a trailing
        # channel digit (e.g. "cf2", "amp3"); with none it defaults to channel 1.
        # The channel is validated against self.numCh, so any channel the
        # instrument actually has (incl. 3/4 on the N5186A) works here.
        setters = {
            "rfState": self.set_rfState,
            "modState": self.set_modState,
            "arbState": self.set_arbState,
            "cf": self.set_cf,
            "amp": self.set_amp,
            "alcState": self.set_alcState,
            "iqScale": self.set_iqScale,
            "rms": self.set_rms,
            "fs": self.set_fs,
        }
        for key, value in kwargs.items():
            if key == "refSrc":
                self.set_refSrc(value)
                continue
            base = key.rstrip("0123456789")
            suffix = key[len(base) :]
            if base not in setters:
                raise KeyError(f'Invalid keyword argument: "{key}"')
            ch = int(suffix) if suffix else 1
            self.channel_checker(ch)
            setters[base](value, ch=ch)

        # Arb state can only be turned on after a waveform has been loaded/selected
        # self.write(f'radio:arb:state {arbState}')
        # self.arbState = self.query('radio:arb:state?').strip()

        self.err_check()

    def channel_checker(self, ch: int) -> None:
        """
        Validates the requested channel against the number of channels this
        instrument actually has (self.numCh), so subclasses with more than two
        channels (e.g. N5186A) are supported.
        Args:
            ch (int): Channel number
        """

        if ch < 1 or ch > self.numCh:
            raise ValueError(f"Invalid channel {ch}. This instrument has {self.numCh} channel(s).")

    def set_rfState(self, rfState: bool, ch: int = 1) -> None:
        """
        Sets and reads the state of the RF output using SCPI commands.
        Args:
            rfState (bool): Turns the RF output on (True) or off (False).
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)
        if not isinstance(rfState, bool):
            raise ValueError('"rfState" must be a bool (True/False).')

        # SCPI expects 1/0, so map the bool explicitly.
        self.write(f"source:rf{ch}:output:state {int(rfState)}")
        setattr(self, f"rfState{ch}", bool(int(self.query(f"source:rf{ch}:output:state?").strip())))

    def set_modState(self, modState: bool, ch: int = 1) -> None:
        """
        Sets and reads the state of the internal baseband modulator output using SCPI commands.
        Args:
            modState (bool): Turns the baseband modulator on (True) or off (False).
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)
        if not isinstance(modState, bool):
            raise ValueError('"modState" must be a bool (True/False).')

        # SCPI expects 1/0, so map the bool explicitly.
        self.write(f"source:rf{ch}:output:modulation {int(modState)}")
        setattr(
            self,
            f"modState{ch}",
            bool(int(self.query(f"source:rf{ch}:output:modulation?").strip())),
        )

    def set_arbState(self, arbState: bool, ch: int = 1) -> None:
        """
        Sets and reads the state of the internal arb waveform generator using SCPI commands.
        Args:
            arbState (bool): Turns the arb waveform generator on (True) or off (False).
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)
        if not isinstance(arbState, bool):
            raise ValueError('"arbState" must be a bool (True/False).')

        # SCPI expects 1/0, so map the bool explicitly.
        self.write(f"source:signal{ch}:state {int(arbState)}")
        setattr(self, f"arbState{ch}", bool(int(self.query(f"source:signal{ch}:state?").strip())))

    def set_cf(self, cf: float, ch: int = 1) -> None:
        """
        Sets and reads the center frequency of the signal generator output using SCPI commands.
        Args:
            cf (float): Sets the generator's carrier frequency.
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)

        # Type checking with a useful error message
        try:
            float(cf)
        except (TypeError, ValueError):
            raise ValueError("Carrier frequency must be a positive floating point value.")
        if cf <= 0:
            raise ValueError("Carrier frequency must be a positive floating point value.")

        self.write(f"source:rf{ch}:frequency {cf}")
        setattr(self, f"cf{ch}", float(self.query(f"source:rf{ch}:frequency?").strip()))

    def set_amp(self, amp: float, ch: int = 1) -> None:
        """
        Sets and reads the output amplitude of signal generator output using SCPI commands.
        Args:
            amp (int/float): Sets the generator's RF output power.
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)

        # Type checking with a useful error message
        try:
            float(amp)
            int(amp)
        except (TypeError, ValueError):
            raise ValueError('"amp" should be a numerical value.')

        self.write(f"source:rf{ch}:power {amp}")
        setattr(self, f"amp{ch}", float(self.query(f"source:rf{ch}:power?").strip()))

    def set_alcState(self, alcState: bool, ch: int = 1) -> None:
        """
        Sets and reads the state of the ALC (automatic level control) output using SCPI commands.
        This should be turned off for narrow pulses and signals with rapid amplitude changes.
        Args:
            alcState (bool): Turns the ALC (automatic level control) on (True) or off (False).
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)
        if not isinstance(alcState, bool):
            raise ValueError('"alcState" must be a bool (True/False).')

        # SCPI expects 1/0, so map the bool explicitly.
        self.write(f"source:rf{ch}:power:alc {int(alcState)}")
        setattr(self, f"alcState{ch}", bool(int(self.query(f"source:rf{ch}:power:alc?").strip())))

    def set_iqScale(self, iqScale: int, ch: int = 1) -> None:
        """
        Sets and reads the scaling of the baseband IQ waveform output using SCPI commands.
        Should be about 70 percent to avoid clipping.
        Args:
            iqScale (int): Scales the IQ modulator in percent. Default/safe value is 70, range is 0 to 100.
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)

        # Type checking with a useful error message
        try:
            int(iqScale)
        except (TypeError, ValueError):
            raise ValueError("iqScale argument must be an integer between 1 and 100.")
        if iqScale <= 0 or iqScale > 100:
            raise ValueError("iqScale argument must be an integer between 1 and 100.")

        self.write(f"source:signal{ch}:waveform:scale {iqScale}")
        setattr(
            self, f"iqScale{ch}", float(self.query(f"source:signal{ch}:waveform:scale?").strip())
        )

    def set_rms(self, rms: float, ch: int = 1) -> None:
        """
        Sets and reads the RMS value of the baseband IQ waveform output using SCPI commands.
        Should be set to 1 for combined pulsed signals.
        Args:
            rms (float): Waveform RMS power calculation. VXG will offset RF power to ensure measured RMS power matches the user-specified RF power.
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)

        # Type checking with a useful error message
        try:
            int(rms)
            float(rms)
        except (TypeError, ValueError):
            raise ValueError('"rms" must be a floating point value between 0.1 and 1.414213562.')
        if rms <= 0.1 or rms > 1.414213562:
            raise ValueError('"rms" must be a floating point value between 0.1 and 1.414213562.')

        self.write(f"source:signal{ch}:waveform:rms {rms}")
        setattr(self, f"rms{ch}", float(self.query(f"source:signal{ch}:waveform:rms?").strip()))

    def set_fs(self, fs: float, ch: int = 1) -> None:
        """
        Sets and reads sample  rate of internal arb using SCPI commands.
        Args:
            fs (float): Sample rate.
            ch (int): Specified channel being adjusted.
        """

        self.channel_checker(ch)

        # Type checking with a useful error message
        try:
            int(fs)
            float(fs)
        except (TypeError, ValueError):
            raise ValueError("Sample rate must be a positive floating point value.")
        if fs <= 0:
            raise ValueError("Sample rate must be a positive floating point value.")

        self.write(f"signal{ch}:waveform:sclock:rate {fs}")
        setattr(self, f"fs{ch}", float(self.query(f"signal{ch}:waveform:sclock:rate?").strip()))

    def set_refSrc(self, refSrc: str) -> None:
        """
        Sets and reads the reference clock source output using SCPI commands.
        Args:
            refSrc (str): Sets the reference clock source. ('int', 'ext', 'bbg')
        """

        if not isinstance(refSrc, str) or refSrc.lower() not in [
            "int",
            "ext",
            "internal",
            "external",
            "bbg",
        ]:
            raise ValueError('"refSrc" must be "internal", "external", or "bbg".')

        self.write(f"roscillator:source {refSrc}")
        self.refSrc = self.query("roscillator:source?").strip()
        if "int" in self.refSrc.lower():
            self.refFreq = 10e6
        elif "ext" in self.refSrc.lower():
            self.refFreq = float(self.query("roscillator:frequency:external?").strip())
        elif "bbg" in self.refSrc.lower():
            self.refFreq = float(self.query("roscillator:frequency:bbg?").strip())
        else:
            raise error.InstrumentError("Unknown refSrc selected.")

    def sanity_check(self) -> None:
        """Logs initialized values."""
        logger.info("RF State 1: %s", self.rfState1)
        logger.info("Modulation State 1: %s", self.modState1)
        logger.info("Center Frequency 1: %s", self.cf1)
        logger.info("Output Amplitude 1: %s", self.amp1)
        logger.info("ALC state 1: %s", self.alcState1)
        logger.info("IQ Scaling 1: %s", self.iqScale1)
        logger.info("RMS 1: %s", self.rms1)
        logger.info("Reference Source: %s", self.refSrc)
        logger.info("Internal Arb1 State: %s", self.arbState1)
        logger.info("Internal Arb1 Sample Rate: %s", self.fs1)

        if self.numCh == 2:
            logger.info("RF State 2: %s", self.rfState2)
            logger.info("Modulation State 2: %s", self.modState2)
            logger.info("Center Frequency 2: %s", self.cf2)
            logger.info("Output Amplitude 2: %s", self.amp2)
            logger.info("ALC state 2: %s", self.alcState2)
            logger.info("IQ Scaling 2: %s", self.iqScale2)
            logger.info("RMS 2: %s", self.rms2)
            logger.info("Internal Arb2 State: %s", self.arbState2)
            logger.info("Internal Arb2 Sample Rate: %s", self.fs2)

    def _write_wfm_block(self, scpiPrefix: str, payload: bytes) -> None:
        """
        Writes a raw waveform byte block through whichever API gate is active.
        PyVISA needs an explicit "B" (unsigned byte) datatype; socketscpi infers
        it from a uint8 numpy array. Mirrors transfer_file()'s binary I/O.
        """
        if self.apiType == "pyvisa":
            self.write_binary_values(scpiPrefix, payload, datatype="B")
        else:
            self.write_binary_values(scpiPrefix, np.frombuffer(payload, dtype=np.uint8))

    # def download_wfm(self, wfmData, wfmID="wfm", sim=False):
    def download_wfm(
        self,
        wfmID: str = "wfm",
        sim: bool = False,
        localPath: str = local_waveform_path,
        remotePath: str = vxg_waveform_path,
    ) -> str:
        """
        Reads a pre-formatted waveform file from localPath/wfmID and downloads it
        into the instrument's waveform memory. The IQ data must already be on
        disk (build it with check_wfm() and iq_wfm_combiner()); this method does
        not accept a NumPy array directly.
        Args:
            wfmID (str): Waveform file name; also the returned identifier.
            sim (bool): If True, download to C:\\Temp instead of the default location.
            localPath (str): Source folder on this PC holding the waveform file.
                Defaults to local_waveform_path.
            remotePath (str): Destination folder on the instrument. Defaults to
                vxg_waveform_path (VXG only; ignored for MXG, which uses SNVWFM:).

        Returns:
            (str): Useful waveform identifier/name. Use this as the waveform identifier for the .play() method.

        Raises:
            error.RemotePathError: if the destination folder does not exist on a
                VXG-family instrument (checked before any data is sent).
        """

        # Stop output before doing anything else. Use the class-consistent
        # source:signal{ch}:state command (not the legacy radio:arb family).
        self.write("source:signal1:state off")
        self.write("source:rf1:output:modulation off")
        self.arbState1 = bool(int(self.query("source:signal1:state?").strip()))

        # Waveform format checking. VXG can only use 'iq' format waveforms.
        # if not isinstance(wfmData, np.ndarray):
        #     raise TypeError("wfmData should be a complex NumPy array.")

        # if wfmData.dtype != complex:
        #     raise TypeError("Invalid wfm type. IQ waveforms must be an array of complex values.")
        # else:
        #     i = self.check_wfm(np.real(wfmData))
        #     q = self.check_wfm(np.imag(wfmData))

        #     wfm = self.iq_wfm_combiner(i, q)

        # try:
        #     self.write(f'mmemory:delete "D:\\Users\\Instrument\\Documents\\Keysight\\PathWave\\SignalGenerator\\Waveforms\\{wfmID}.bin"')
        #     self.query('*opc?')
        #     self.err_check()
        # except socketscpi.SockInstError:
        #     print('Waveform doesn\'t exist, skipping delete operation.')
        # pass

        # self.write(f'source:signal:waveform:select "D:\\Users\\Instrument\\Documents\\Keysight\\PathWave\\SignalGenerator\\Waveforms\\{wfmID}.bin"')

        # self.write_binary_values(
        #     f'mmemory:data "D:\\Users\\Instrument\\Documents\\Keysight\\PathWave\\SignalGenerator\\Waveforms\\{wfmID}.bin",',
        #     wfm,
        # )
        # os.path.join keeps the local read working off-Windows (e.g. the dev Mac);
        # the remote paths stay backslashed because the instrument runs Windows.
        with open(os.path.join(localPath, wfmID), "rb") as local_file:
            wfm = local_file.read()
        # Save waveform to specified location on hard drive.
        if sim:
            logger.info("downloading waveform %s to C:\\Temp (sim)", wfmID)
            self._write_wfm_block(f'mmemory:data "C:\\Temp\\{wfmID}",', wfm)
        else:
            if self.sgGroup == "vxg":
                # Fail fast with a clear message if the folder is missing (-257),
                # rather than after sending the whole waveform.
                self._require_remote_dir(f"{remotePath}\\{wfmID}")
                logger.info("downloading waveform %s to %s", wfmID, remotePath)
                self._write_wfm_block(f'mmemory:data "{remotePath}\\{wfmID}",', wfm)
            elif self.sgGroup == "mxg":
                logger.info("downloading waveform %s to SNVWFM:", wfmID)
                self._write_wfm_block(f'mmemory:data "SNVWFM:{wfmID}",', wfm)

        return wfmID

    @staticmethod
    def iq_wfm_combiner(i: np.ndarray, q: np.ndarray) -> np.ndarray:
        """
        Combines i and q wfms into a single interleaved wfm for download to generator.
        Args:
            i (NumPy array): Array of real waveform samples.
            q (NumPy array): Array of imaginary waveform samples.

        Returns:
            (NumPy array): Array of interleaved IQ values.
        """

        iq = np.empty(2 * len(i), dtype=np.int16)
        iq[0::2] = i
        iq[1::2] = q
        return iq

    def check_wfm(self, wfm: np.ndarray) -> np.ndarray:
        """
        HELPER FUNCTION
        Checks minimum size and granularity and returns waveform with
        appropriate binary formatting. Note that sig gens expect big endian
        byte order.

        See pages 205-256 in Keysight X-Series Signal Generators Programming
        Guide (November 2014 Edition) for more info.
        Args:
            wfm (NumPy array): Unscaled/unformatted waveform data.

        Returns:
            (NumPy array): Waveform data that has been scaled and
                formatted appropriately for download to AWG
        """

        # If waveform length doesn't meet granularity or minimum length requirements, repeat the waveform until it does
        repeats = wraparound_calc(len(wfm), self.gran, self.minLen)
        wfm = np.tile(wfm, repeats)
        rl = len(wfm)
        if rl < self.minLen:
            raise error.InstrumentError(f"Waveform length: {rl}, must be at least {self.minLen}.")
        if rl % self.gran != 0:
            raise error.GranularityError(f"Waveform must have a granularity of {self.gran}.")

        return np.array(self.binMult * wfm, dtype=np.int16).byteswap()

    def delete_wfm(self, wfmID: str, remotePath: str = vxg_waveform_path) -> None:
        """
        Stops output and deletes specified waveform from the instrument.
        Args:
            wfmID (str): Waveform filename to delete (no path).
            remotePath (str): Folder on the instrument containing the waveform.
                Defaults to vxg_waveform_path.
        """

        self.stop()
        if "M938" in self.instId:
            # M9383A uses a flat memory namespace
            self.write(f'memory:delete "{wfmID}"')
        elif self.sgGroup == "mxg":
            # MXG/N5186A stores waveforms in the SNVWFM: namespace
            self.write(f'mmemory:delete "SNVWFM:{wfmID}"')
        else:
            # VXG family: waveforms are files; delete by full path
            self.write(f'mmemory:delete "{remotePath}\\{wfmID}"')
        self.query("*OPC?")
        self.err_check()

    def clear_all_wfm(self) -> None:
        """Stops output and deletes all iq waveforms."""
        self.stop()
        self.write("mmemory:delete:wfm")
        self.err_check()

    def play(
        self,
        wfmID: str = "wfm",
        ch: int = 1,
        sim: bool = False,
        remotePath: str = vxg_waveform_path,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """
        Selects waveform and activates arb mode, RF output, and modulation.
        Args:
            wfmID (str): Waveform identifier, used to select waveform to be played.
            ch (int): Specified channel being adjusted.
            remotePath (str): Folder on the instrument from which to select the
                waveform. Defaults to vxg_waveform_path.
        **kwargs:
            rms (float): Waveform RMS power calculation. VXG will offset RF power to ensure measured RMS power matches the user-specified RF power.
        """

        self.channel_checker(ch)

        # Load waveform from where download_wfm() put it. MXG/N5186A stores
        # waveforms in the SNVWFM: namespace, not a filesystem folder.
        if sim:
            logger.info("playing waveform %s from C:\\Temp on channel %d (sim)", wfmID, ch)
            self.write(f'source:signal{ch}:waveform:select "C:\\Temp\\{wfmID}.bin"')
        elif self.sgGroup == "mxg":
            logger.info("playing waveform %s from SNVWFM: on channel %d", wfmID, ch)
            self.write(f'source:signal{ch}:waveform:select "SNVWFM:{wfmID}"')
        else:
            logger.info("playing waveform %s from %s on channel %d", wfmID, remotePath, ch)
            self.write(f'source:signal{ch}:waveform:select "{remotePath}\\{wfmID}"')

        # Turn on arb, RF output and modulation
        self.set_arbState(True, ch)
        self.set_rfState(True, ch)
        self.set_modState(True, ch)

        # Don't know why, but the VXG uses a weird sample rate number when the waveform is selected, so we use the one we set in .configure()
        self.set_fs(getattr(self, f"fs{ch}"), ch)

        # The RMS value set by .configure() is overwritten by the VXG's internal calculation when waveform is selected, so we will apply the one we set in .configure() if the 'rms' keyword arg is present.
        for key, value in kwargs.items():
            if key == "rms":
                self.set_rms(value, ch=ch)

        self.err_check()

    def stop(self, ch: int = 1) -> None:
        """
        Dectivates arb mode, RF output, and modulation per channel.
        Args:
            ch (int): Channel on which to stop playback.
        """

        self.channel_checker(ch)

        self.set_arbState(False, ch=ch)
        self.set_rfState(False, ch=ch)
        self.set_modState(False, ch=ch)

    def set_SG_mode(self, mode: str, ch: int = 1) -> None:
        """
        Sets the signal generator mode.
        Args:
            mode (str): The mode to set ("MTON", "WAV").
            ch (int): The channel on which to set the mode.
        """
        self.channel_checker(ch)
        self.write(f"source:signal{ch}:mode {mode}")

    def configure_multitone(self, tones: int, Tone_space: float, ch: int = 1) -> None:
        """
        Configures the signal generator for multitone operation.
        Args:
            tones (int): The number of tones to configure.
            Tone_space (float): The spacing between tones.
            ch (int): The channel on which to configure the tones.
        """
        self.channel_checker(ch)
        self.write(f"source:signal{ch}:MTON:ARB:NTON {tones}")
        self.write(f"source:signal{ch}:MTON:ARB:FSP {Tone_space}")
        self.write(f"source:signal{ch} 1")
        self.write(f"RF{ch}:OUTP:MOD 1")
        self.write(f"RF{ch}:OUTP 1")

class N5186A(VXG):
    def __init__(
        self,
        ipAddress: str,
        apiType: str = "socketscpi",
        timeout: int = 10,
        reset: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Generic class for controlling the N5186A Vector MXG signal generator.

        Attributes:
            rf1State (bool): Turns the RF output on or off. (True, False)
            modState (bool): Turns the baseband modulator on or off. (True, False)
            cf (float): Sets the generator's carrier frequency.
            amp (int/float): Sets the generator's RF output power.
            alcState (bool): Turns the ALC (automatic level control) on or off. (True, False)
            iqScale (int): Scales the IQ modulator. Default/safe value is 70
            refSrc (str): Sets the reference clock source. ('int', 'ext', 'bbg')
            fs (float): Sets the sample rate of the baseband generator.

        TODO
            Add check to ensure that the correct instrument is connected
        """

        # super().__init__(host, port, timeout)
        super().__init__(ipAddress, apiType=apiType, timeout=timeout, **kwargs)
        if reset:
            self.write("*rst")
            self.query("*opc?")

        # Set the byte order to big endian
        self.write(":SYSTem:WAVeform:BFILe:FORMat:DEFault I16Big")

        # query the options on the MXG to see how many channels it has
        self.numCh = 0
        for ch in range(1, 5):
            optionString = self.query(f"system:rf{ch}:opt?").strip()
            if optionString != '""':
                self.numCh += 1
        logger.info("Number of channels: %d", self.numCh)

        for ch in range(1, self.numCh + 1):
            logger.debug("querying channel %d", ch)
            # Binary states stored as bool (see VXG.__init__) for correct truthiness.
            setattr(self, f"rfState{ch}", bool(int(self.query(f"rf{ch}:output?").strip())))
            setattr(
                self,
                f"modState{ch}",
                bool(int(self.query(f"rf{ch}:output:modulation?").strip())),
            )
            setattr(self, f"cf{ch}", float(self.query(f"source:rf{ch}:frequency?").strip()))
            setattr(self, f"amp{ch}", float(self.query(f"rf{ch}:power?").strip()))
            setattr(
                self,
                f"arbState{ch}",
                bool(int(self.query(f"source:group{ch}:signal:state?").strip())),
            )
            # N5186A has no ALC hardware; querying alc? returns +703 error. Track locally as False.
            setattr(self, f"alcState{ch}", False)
            setattr(
                self,
                f"iqScale{ch}",
                float(self.query(f"source:group{ch}:signal:waveform:scale?").strip()),
            )
            setattr(
                self,
                f"rms{ch}",
                float(self.query(f"source:group{ch}:signal:waveform:rms?").strip()),
            )
            setattr(
                self,
                f"fs{ch}",
                float(self.query(f"source:group{ch}:signal:waveform:sclock:rate?").strip()),
            )

    # def transfer_wfmFile(self,fileName):
    ##

    def set_alcState(self, alcState: bool, ch: int = 1) -> None:
        """N5186A has no ALC hardware (+703 if queried). Track state locally only."""
        if not isinstance(alcState, bool):
            raise ValueError('"alcState" must be a bool (True/False).')
        self.channel_checker(ch)
        logger.warning(
            "N5186A has no ALC hardware — set_alcState(%s, ch=%d) is a no-op", alcState, ch
        )
        setattr(self, f"alcState{ch}", bool(alcState))

    def recall_state(self, fileName: str) -> None:
        if not isinstance(fileName, str) or not fileName.strip():
            raise ValueError("Filename must be a non-empty string.")

        # Set timeout to >30 if not already
        _timeout = self.instance.timeout
        if _timeout < 30000:
            self.instance.timeout = 30000

        self.write(f"memory:state:recall '{fileName}'")

        self.query("*OPC?")

        # restore timeout
        self.instance.timeout = _timeout
