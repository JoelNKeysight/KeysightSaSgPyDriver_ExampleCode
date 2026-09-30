"""

IMPORTANT: If the Software includes one or more computer programs bearing a Keysight copyright notice and in source code format (“Source Files”), such Source Files are 
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
Author:  Keysight Technologies
Builds instrument specific classes for each signal analyzer.
The classes include mode selection, measurement setup/fetch (band power, ACP,
phase noise, IQ capture, EVM), state-file recall, and file transfer per instrument.
Tested on N9042B (UXA) and N9032B (PXA).
"""

import logging
import os
from typing import Any

import numpy as np
import pyvisa
import socketscpi  # type: ignore[import-untyped]  # socketscpi ships no type stubs

import error

logger = logging.getLogger(__name__)





class SignalAnalyzerBase:
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
                r"D:\users\Instrument\Documents\VMA\state\foo.bin".
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
        Note: class attributes queried at connect time (saMode, cf, etc.)
        are not refreshed and may no longer match the instrument state.
        """

        self.write("*RST")
        self.wait_for_opc()
        self.err_check()

    def print_capabilities(self) -> None:
        """Prints instrument identification, installed options, and available measurement modes."""

        logger.info("Instrument ID: %s", self.instId.strip())
        logger.info("Installed options: %s", self.query("*OPT?").strip())
        logger.info("Available modes: %s", self.query("INST:CAT?").strip())


# noinspection PyUnresolvedReferences
class UXA(SignalAnalyzerBase):
    # Default destination folder on the instrument for transferred state files.
    # Override per call via transfer_file(remotePath=...) or per instance by
    # reassigning this attribute (e.g. for a non-default install path).
    defaultRemoteStatePath = r"D:\users\Instrument\Documents\VMA\state"

    def __init__(
        self,
        ipAddress: str,
        apiType: str = "socketscpi",
        timeout: int = 10,
        reset: bool = False,
        **kwargs: Any,
    ) -> None:
        """
        Generic class for controlling the X series
        family signal analyzers.

        Attributes:

            modState (int): Turns the baseband modulator on or off. (1, 0)
            cf (float): Sets the analyzer's carrier frequency.

            refSrc (str): Sets the reference clock source. ('int', 'ext', 'bbg')


        TODO
            Add check to ensure that the correct instrument is connected
        """

        super().__init__(ipAddress, apiType=apiType, timeout=timeout, **kwargs)
        if reset:
            self.write("*rst")
            self.query("*opc?")

        # Query all settings from UXA and store them as class attributes

        self.saMode = self.query("instrument:select?").strip()
        self.saMeasurement = self.query(":CONFigure?").strip()

        # Always define self.cf so later code can rely on it. PNOISE/BASIC boots
        # don't have a carrier to query here; the measurement-setup methods
        # populate the correct value when called.
        self.cf = 0.0
        if self.saMode == "SA":
            self.cf = float(self.query("sense:frequency:center?").strip())
        elif self.saMode == "VMA":
            self.cf = float(self.query("sense:ofdm:ccarrier:reference?").strip())

        self.refSrc = self.query("sense:roscillator:source:type?").strip()

        if "int" in self.refSrc.lower():
            self.refFreq = 10e6
        elif "ext" in self.refSrc.lower():
            self.refFreq = float(self.query("sense:roscillator:external:frequency?").strip())
        elif "sens" in self.refSrc.lower():
            self.refFreq = float(self.query("sense:roscillator:external:frequency?").strip())
        else:
            raise error.InstrumentError("Unknown refSrc selected.")

    # def configure(self, rfState=1, modState=1, cf=1e9, amp=-20, alcState=0, iqScale=70, refSrc='int', fs=200e6):
    def configure(self, **kwargs: Any) -> None:
        """
        Sets basic configuration for the analyzer and populates class attributes
        accordingly. Only the keyword arguments listed below are accepted; any
        other key raises KeyError.
        Keyword Arguments:
            saMode (str): Measurement mode ("SA", "VMA", "PNOISE", "BASIC").
            cf (float): Sets the analyzer's center/carrier frequency.
            refSrc (str): Sets the reference clock source. ('int', 'ext', 'sense')
        """

        # Check to see which keyword arguments the user sent and call the appropriate function
        for key, value in kwargs.items():

            if key == "saMode":
                self.set_saMode(value)
            elif key == "cf":
                self.set_cf(value)

            elif key == "refSrc":
                self.set_refSrc(value)

            else:
                raise KeyError(
                    f'Invalid keyword argument: "{key}"'
                )  # raise KeyError('Invalid keyword argument.')

        # Arb state can only be turned on after a waveform has been loaded/selected
        # self.write(f'radio:arb:state {arbState}')
        # self.arbState = self.query('radio:arb:state?').strip()

        self.err_check()

    def set_saMode(self, saMode: str) -> None:
        """
        Sets and reads the state of the internal baseband modulator output using SCPI commands.
        Args:
            saMode (string): Sets the measurement mode
        """

        if saMode not in ["SA", "VMA", "PNOISE", "BASIC"]:
            raise ValueError('Only "SA", "VMA", "PNOISE", and "BASIC" currently supported')

        self.write(f"instrument:select {saMode}")
        self.saMode = self.query("instrument:select?").strip()

    def set_cf(self, cf: float) -> None:
        """
        Sets and reads the center/carrier frequency of the analyzer using SCPI commands.
        The SCPI node depends on the active mode: VMA uses the OFDM carrier
        reference, PNOISE uses the carrier frequency, SA/BASIC use the center
        frequency.
        Args:
            cf (float): Sets the analyzer's center/carrier frequency (Hz). Ints accepted.
        """

        if isinstance(cf, bool) or not isinstance(cf, (int, float)) or cf <= 0:
            raise ValueError("Carrier frequency must be a positive number.")
        cf = float(cf)

        self.saMode = self.query("instrument:select?").strip()

        # The right SCPI node depends on the active measurement mode.
        if self.saMode == "VMA":
            meas = self.query("configure?").strip().strip('"')
            if meas == "ACP":
                self.write(f"sense:frequency:center {cf}")
                self.cf = float(self.query("sense:frequency:center?").strip())
            else:
                self.write(f"sense:ofdm:ccarrier:reference {cf}")
                self.cf = float(self.query("sense:ofdm:ccarrier:reference?").strip())
        elif self.saMode == "PNOISE":
            self.write(f"sense:frequency:carrier {cf}")
            self.cf = float(self.query("sense:frequency:carrier?").strip())
        else:  # SA, BASIC
            self.write(f"sense:frequency:center {cf}")
            self.cf = float(self.query("sense:frequency:center?").strip())

    def set_refSrc(self, refSrc: str) -> None:
        """
        Sets and reads the reference clock source output using SCPI commands.
        Args:
            refSrc (str): Sets the reference clock source. ('int', 'ext', 'sense')
        """

        if not isinstance(refSrc, str) or refSrc.lower() not in [
            "int",
            "ext",
            "internal",
            "external",
            "sense",
        ]:
            raise ValueError('"refSrc" must be "internal", "external", or "sense".')

        self.write(f"sense:roscillator:source:type {refSrc}")
        self.refSrc = self.query("sense:roscillator:source:type?").strip()
        if "int" in self.refSrc.lower():
            self.refFreq = 10e6
        elif "ext" in self.refSrc.lower():
            self.refFreq = float(self.query("sense:roscillator:external:frequency?").strip())
        elif "sense" in self.refSrc.lower():
            self.refFreq = float(self.query("sense:roscillator:external:frequency?").strip())
        else:
            raise error.InstrumentError("Unknown refSrc selected.")

    def get_state_files(self, remotePath = None):
        r"""
        Check current existing state files in SA.

        Args:
        remotePath (str, optional): Destination path on the instrument. If omitted,
            the file is placed in the default state folder
            (D:\users\Instrument\Documents\VMA\state).

        Returns:
        list of state file string.
        """

        # Can still pass an explicit destination anytime, which overrides the default
        if remotePath is None:
            remotePath = self.defaultRemoteStatePath
        
        _timeout = self.instance.timeout
        self.instance.timeout = 30000

        try:
            # Sends the file contents as an IEEE 488.2 binary block; the instrument
            # writes it to remotePath via the MMEM:DATA command.
            fileListString = self.query(f':MMEM:CAT? "{remotePath}"')
            fileListString = fileListString.strip().replace('"', '')
            print(f"Raw file list string from instrument: {fileListString}")
            stateFiles = fileListString.split(',')

            stateFiles = [x for x in stateFiles if x != '']
            stateFiles = [x for x in stateFiles if x.find('state') != -1]
            print(f"Parsed state file list: {stateFiles}")
            return stateFiles
        
        finally:
            # Always restore the original timeout, even if the transfer fails
            self.instance.timeout = _timeout

        self.err_check()


    def recall_state(self, fileName: str) -> None:
        """
        Recalls a statefile located on the instrument
        Args:
            fileName (str): filename, including file path, located on the instrument
            can be a .state or .screen file
            does not contain an error check for succesful load
        """
        if not isinstance(fileName, str) or not fileName.strip():
            raise ValueError("Filename must be a non-empty string.")

        # Split into (root, extension)
        _, ext = os.path.splitext(fileName.strip())

        # Set timeout to >30 if not already
        _timeout = self.instance.timeout
        if _timeout < 30000:
            self.instance.timeout = 30000

        if ext.lower() == ".state":
            self.write(f"mmemory:load:state '{fileName}'")
        elif ext.lower() == ".screen":
            self.write(f"mmemory:load:sconfig '{fileName}'")

        self.query("*OPC?")

        # restore timeout
        self.instance.timeout = _timeout

    def _require_mode(self, mode: str, methodName: str) -> None:
        """Raises a clear error if the analyzer is not in the required measurement mode."""

        current = self.query("instrument:select?").strip()
        if current != mode:
            raise error.InstrumentError(
                f"{methodName} requires {mode} mode, but the analyzer is in {current} mode. "
                f'Switch modes first (e.g. set_saMode("{mode}") or recall a {mode} state file).'
            )

    def optimize_evm(self) -> None:
        r"""
        Auto-ranges the analyzer for minimum EVM, then waits for completion.
        Requires VMA mode with a custom OFDM state file loaded via recall_state().
        Verified SCPI: SENSE:OFDM:OPTimize on N9042B firmware A.43.50.
        """
        self._require_mode("VMA", "optimize_evm()")
        self.write(":SENSe:OFDM:OPTimize")
        self.query("*OPC?")
        self.err_check()

    def query_evm(self, numCarriers: int) -> list[float]:
        r"""
        Reads Carrier power (dBm) and RMS EVM (%) for each component carrier from the VMA custom OFDM measurement.
        Requires VMA mode with a custom OFDM state file loaded via recall_state().

        Result block per carrier: FETCH:OFDM{100*cc+1}? returns 19 float32 values;
        index 0 is EVM RMS %, verified live on N9042B with 2CC 256QAM signal.
        index 12 is Tx power dBm

        Args:
            numCarriers (int): Number of component carriers to read (>= 1, max 8).

        Returns:
            list[float]: Interleaved CC power (dBm) and RMS EVM % per carrier, ordered CC0 .. CC(numCarriers-1).
        """
        if not isinstance(numCarriers, int) or numCarriers < 1:
            raise ValueError("numCarriers must be a positive integer.")
        self._require_mode("VMA", "query_evm()")

        evm = []
        for c in range(numCarriers):
            n = 100 * c + 1
            data = self._read_binary(f"FETCH:OFDM{n}?")
            evm.append(float(data[12]))
            evm.append(float(data[0]))
        self.err_check()
        return evm

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

    def transfer_file(self, localPath: str, remotePath: str | None = None) -> None:
        r"""
        Transfers a file from the local PC to the instrument via MMEM:DATA.
        Works through both API gates (pyvisa and socketscpi).

        Args:
        localPath (str): Path to the source file on this PC.
        remotePath (str, optional): Destination path on the instrument. If omitted,
            the file is placed in the default state folder
            (self.defaultRemoteStatePath).

        Raises:
        error.RemotePathError: if the destination folder does not exist on the
            instrument (checked before any data is sent).
        """

        # Can still pass an explicit destination anytime, which overrides the default
        if not isinstance(localPath, str) or not localPath.strip():
            raise ValueError("localPath must be a non-empty string.")

        if not os.path.isfile(localPath):
            raise ValueError(f"local file does not exist: {localPath}")

        if remotePath is None:
            fileName = os.path.basename(localPath)
            remotePath = self.defaultRemoteStatePath + "\\" + fileName

        # Default destination: instrument's VMA state folder + the local filename
        if not isinstance(remotePath, str) or not remotePath.strip():
            raise ValueError("remotePath must be a non-empty string.")

        # Fail fast with a clear message if the destination folder is missing,
        # rather than after sending the whole file and hitting a SCPI -257.
        self._require_remote_dir(remotePath)

        logger.info("transferring %s to %s", localPath, remotePath)

        _timeout = self.instance.timeout
        self.instance.timeout = 30000

        try:
            # Read the local file as raw bytes
            with open(localPath, "rb") as f:
                fileData = f.read()

            # The instrument writes it to remotePath via the MMEM:DATA command.
            if self.apiType == "pyvisa":
                # 'B' = unsigned byte
                self.instance.write_binary_values(
                    f'MMEM:DATA "{remotePath}",', fileData, datatype="B"
                )
            else:
                self.instance.write_binary_values(
                    f'MMEM:DATA "{remotePath}",', np.frombuffer(fileData, dtype=np.uint8)
                )
            self.query("*OPC?")  # wait for the write to complete

        finally:
            # Always restore the original timeout, even if the transfer fails
            self.instance.timeout = _timeout

        self.err_check()
    
    def set_continuous_measurement(self, continuous: bool):
        """
        Sets SA measurement in single mode or continuous mode
        Args:
            continuous (bool): Sets whether the SA is in continuous mode
        """

        if not isinstance(continuous,bool):
            raise ValueError('continuous" should be bool')
        
        self.write(f":INITiate:CONTinuous {int(continuous)}")
        self.continuous = self.query(":INITiate:CONTinuous?").strip()

    def load_ACP_state_file(self, fileName: str) -> None:
        """
        Recalls a state file that configures an ACP measurement and verifies
        the active measurement is ACP afterwards.
        Args:
            fileName (str): State file path on the instrument (.state).
        """

        self.recall_state(fileName)
        meas = self.query("configure?").strip().strip('"')
        if meas != "ACP":
            raise error.InstrumentError(
                f'State file "{fileName}" did not select an ACP measurement (active measurement: {meas}).'
            )
        self.err_check()

    def query_ACP_table(self) -> dict[str, Any]:
        """
        Triggers a single sweep and returns the ACP result table.
        Requires an ACP measurement to be active (load_ACP_state_file() or CONF:ACP).

        Returns:
            dict with keys:
                totalCarrierPower (float): Total carrier power in dBm.
                refCarrierPower (float): Reference carrier power in dBm.
                offsets (list[dict]): One entry per enabled offset (A-F), each with
                    relLower/relUpper (dBc) and absLower/absUpper (dBm).
                    Disabled offset slots are skipped.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        vals = [float(v) for v in self.query("fetch:acp1?").strip().split(",")]


        self.err_check()

        if len(vals) == 3:
            # Exactly 1 offset enabled: instrument returns a compact 3-value response
            # (carrier_power_dBm, rel_lower_dBc, rel_upper_dBc) with no absolute offset
            # powers. Derive absolute values from carrier + relative.
            carrier, relLower, relUpper = vals
            offsets = [
                {
                    "relLower": relLower,
                    "absLower": carrier + relLower,
                    "relUpper": relUpper,
                    "absUpper": carrier + relUpper,
                }
            ]
            return {"totalCarrierPower": carrier, "refCarrierPower": carrier, "offsets": offsets}

        # 0 or 2+ offsets enabled: 4 header values then 6 offset slots (A-F) of 4 values
        # each. Disabled slots are padded with -999 by the instrument.
        offsets = []
        for k in range(6):
            relLower, absLower, relUpper, absUpper = vals[4 + 4 * k : 8 + 4 * k]
            if relLower == -999.0:
                continue
            offsets.append(
                {
                    "relLower": relLower,
                    "absLower": absLower,
                    "relUpper": relUpper,
                    "absUpper": absUpper,
                }
            )

        return {"totalCarrierPower": vals[1], "refCarrierPower": vals[3], "offsets": offsets}

    def set_band_power_marker(
        self, center_frequency: float, span: float, waveform_bandwidth: float
    ) -> None:
        """
        Configures a band power marker in SA mode. Sets the analyzer center
        frequency and span, places marker 1 at the center frequency, and sets
        its integration bandwidth to the waveform bandwidth.
        Args:
            center_frequency (float): Center of the band power measurement in Hz.
            span (float): Analyzer display span in Hz. Must be >= waveform_bandwidth.
            waveform_bandwidth (float): Integration bandwidth of the band power marker in Hz.
        """

        for name, value in [
            ("center_frequency", center_frequency),
            ("span", span),
            ("waveform_bandwidth", waveform_bandwidth),
        ]:
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a positive number.")
        if waveform_bandwidth > span:
            raise ValueError("waveform_bandwidth cannot exceed span.")

        if self.saMode != "SA":
            self.set_saMode("SA")

        # Select the swept spectrum measurement explicitly; SA mode may have a
        # different measurement (e.g. ACP) active from earlier work.
        if self.query("configure?").strip().strip('"') != "SAN":
            self.write("configure:sanalyzer")

        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f"sense:frequency:span {span}")
        self.write("calculate:marker1:mode position")
        self.write(f"calculate:marker1:x {center_frequency}")
        self.write("calculate:marker1:function bpower")
        self.write(f"calculate:marker1:function:band:span {waveform_bandwidth}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def query_band_power_marker(self) -> float:
        """
        Triggers a single sweep and returns the band power marker result in dBm.
        Requires set_band_power_marker() to have been called first.
        .
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        power = float(self.query("calculate:marker1:y?").strip())

        self.err_check()
        return power

    def setup_PN_measurement(
        self, center_frequency: float, start_offset: float, stop_offset: float
    ) -> None:
        """
        Switches to Phase Noise mode and configures a Log Plot measurement.
        Requires a CW carrier at center_frequency; a modulated signal will
        produce a "Carrier(s) Incorrect or Missing" error during the sweep.
        Args:
            center_frequency (float): Carrier frequency in Hz.
            start_offset (float): Start offset from the carrier in Hz.
            stop_offset (float): Stop offset from the carrier in Hz.
        """

        for name, value in [
            ("center_frequency", center_frequency),
            ("start_offset", start_offset),
            ("stop_offset", stop_offset),
        ]:
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a positive number.")
        if start_offset >= stop_offset:
            raise ValueError("start_offset must be less than stop_offset.")

        if self.saMode != "PNOISE":
            self.set_saMode("PNOISE")

        self.write("configure:lplot")
        self.write(f"sense:frequency:carrier {center_frequency}")
        self.write(f"sense:lplot:frequency:offset:start {start_offset}")
        self.write(f"sense:lplot:frequency:offset:stop {stop_offset}")

        self.cf = float(self.query("sense:frequency:carrier?").strip())
        self.err_check()

    def query_PN(self) -> dict[str, Any]:
        """
        Triggers a single Log Plot sweep and returns the phase noise results.
        Requires setup_PN_measurement() to have been called first.
        Returns:
            dict with keys:
                carrierPower (float): Carrier power in dBm.
                carrierFreq (float): Measured carrier frequency in Hz.
                offsets (np.ndarray): Offset frequencies in Hz.
                phaseNoise (np.ndarray): Smoothed phase noise in dBc/Hz at each offset.
        """

        # Phase noise sweeps are slow; bump timeout like recall_state() does
        _timeout = self.instance.timeout
        if _timeout < 120000:
            self.instance.timeout = 120000

        self.write("initiate:continuous off")
        try:
            # perform auto-tune which will find carrier frequency and adjust SA range
            self.write("sense:frequency:carrier:search")
            self.query("*OPC?")

            # LPL1 is the summary table: carrier power, carrier freq, RMS noise/jitter values
            summary = [float(v) for v in self.query("fetch:lplot1?").strip().split(",")]
            # LPL4 is the smoothed log plot trace as interleaved offset/dBc pairs
            trace = np.array([float(v) for v in self.query("fetch:lplot4?").strip().split(",")])
        finally:
            # Always  the original timeout.
            self.instance.timeout = _timeout
        self.err_check()

        return {
            "carrierPower": summary[0],
            "carrierFreq": summary[1],
            "offsets": trace[0::2],
            "phaseNoise": trace[1::2],
        }

    def capture_IQ(
        self, frequency: float, sample_rate: float, power_range: float, length: int
    ) -> np.ndarray:
        """
        Captures IQ data using the IQ Analyzer (BASIC) mode Waveform measurement.
        Args:
            frequency (float): Center frequency in Hz.
            sample_rate (float): IQ sample rate in Hz. The analyzer couples its
                digital IF bandwidth to this value (IF BW = 0.8 * sample rate).
            power_range (float): Expected maximum input power in dBm. Applied as
                the reference level with input attenuation set to auto.
            length (int): Number of IQ samples to capture.
        Returns:
            np.ndarray: Complex IQ samples in volts (length samples).
        """

        for name, value in [("frequency", frequency), ("sample_rate", sample_rate)]:
            if not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be a positive number.")
        if not isinstance(length, int) or length <= 0:
            raise ValueError("length must be a positive integer.")
        if not isinstance(power_range, (int, float)):
            raise ValueError("power_range must be a number (dBm).")

        if self.saMode != "BASIC":
            self.set_saMode("BASIC")

        self.write("configure:waveform")
        self.write(f"sense:frequency:center {frequency}")
        self.write(f"sense:waveform:srate {sample_rate}")
        self.write(f"sense:waveform:sweep:time {length / sample_rate}")
        self.write("sense:power:rf:attenuation:auto on")
        self.write(f"display:waveform:view:window:trace:y:rlevel {power_range}")
        self.write("initiate:continuous off")
        self.err_check()

        # Long captures can exceed the default timeout
        _timeout = self.instance.timeout
        if _timeout < 60000:
            self.instance.timeout = 60000

        try:
            # READ:WAV0? returns interleaved I/Q float32 values in volts
            data = self._read_binary("read:waveform0?")
        finally:
            # Always restore the original timeout 
            self.instance.timeout = _timeout
           
        self.err_check()

        self.cf = float(self.query("sense:frequency:center?").strip())
        iq = np.array(data[0::2]) + 1j * np.array(data[1::2])
        return iq[:length]

    def sanity_check(self) -> None:
        """Logs user-accessible class attributes."""
        logger.info("SA Mode: %s", self.saMode)
        logger.info("Center Frequency: %s", self.cf)
        logger.info("Reference Source: %s", self.refSrc)

    # marker 
    def set_marker(self, marker_number: int, frequency: float, trace_number: int = 1) -> float:
        """
        Sets the specified marker to the given frequency.
        Args:
            marker_number (int): Marker number (1-10).
            frequency (float): Frequency in Hz.
        """

        if not isinstance(marker_number, int) or not (1 <= marker_number <= 10):
            raise ValueError("marker_number must be an integer between 1 and 10.")
        if not isinstance(frequency, (int, float)) or frequency <= 0:
            raise ValueError("frequency must be a positive number.")
        
        self.write(f":CALCulate:MARKer{marker_number}:TRACe {trace_number}")

        self.write(f":CALCulate:MARKer{marker_number}:MODE POSition")

        self.write(f"calculate:marker{marker_number}:x {frequency}")

        amplitude = float(self.query(f":CALCulate:MARKer{marker_number}:Y?").strip())

        self.err_check()

        return amplitude

    def query_peak_marker(self, marker_number: int) -> float:
        """
        Queries the frequency of the specified peak marker.
        Args:
            marker_number (int): Marker number (1-10).
        Returns:
            tuple[float, float]: Frequency and amplitude in Hz and dBm respectively.
        """

        if not isinstance(marker_number, int) or not (1 <= marker_number <= 10):
            raise ValueError("marker_number must be an integer between 1 and 10.")

        self.write(f":CALCulate:MARKer{marker_number}:MAXimum:PEAK")

        frequency = float(self.query(f":CALCulate:MARKer{marker_number}:X?").strip())
        amplitude = float(self.query(f":CALCulate:MARKer{marker_number}:Y?").strip())

        self.err_check()

        return frequency, amplitude

    # Standard SA measurements
    def optimize_SA_Attenuation(self) -> None:
        """
        Optimizes the analyzer's attenuation setting.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write(f":SENSe:POWer:RF:RANGe:OPTimize IMMediate")

    def set_SA_Attenuation(self, attenuation: float) -> None:
        """
        Sets the analyzer's attenuation setting.
        Args:
            attenuation (float): Attenuation value in dB.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write(f":SENSe:POWer:RF:ATTenuation {attenuation}")

    def set_SA_trigger(self,measurement:str, trigger_type: str, level: float) -> None:
        """
        Sets the analyzer's trigger setting.
        Args:
            measurement (str): The measurement for which to set the trigger ("CHP", "OBW", "ACP", "PST", "TXP", "SPUR", "SEM", "TOI", "HARM", "PAVT").
            trigger_type (str): Trigger type ("IMM", "LINE", "RFB", "FRAM", "EXT1", "EXT2").
            level (float): Trigger level in dBm for RFB/VID, V for EXT1/EXT2.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")
        
        if measurement in ["CHP", "OBW", "ACP", "PST", "TXP", "SPUR", "SEM", "TOI", "HARM", "PAVT"]:
            if trigger_type in ["IMM", "VID", "LINE", "RFB", "FRAM", "EXT1", "EXT2"]:
                self.write(f":TRIGger:{measurement}:SEQuence:SOURce {trigger_type}")
            else:
                raise ValueError("Invalid trigger type.")
            if trigger_type in ["VID", "RFB", "EXT1", "EXT2"]:
                self.write(f":TRIGger:{measurement}:LEVel {level}")
        else:
            raise ValueError("Invalid measurement type.")
    
    def set_SA_Continuous_measurement(self, Continuous: bool) -> None:
        """
        Configures the analyzer for continuous measurement.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write(":INITiate:CONTinuous {Continuous}")

    def set_SA_SweepSpectrum_measurement(self, center_frequency: float, span: float) -> None:
        """
        Configures the analyzer for a swept spectrum measurement.
        Args:
            center_frequency (float): Center frequency in Hz.
            span (float): Analyzer display span in Hz.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write("CONFigure:SAN")
        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f":SENSe:FREQuency:SPAN {span}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def config_SA_SweepSpectrum_RBW(self, RBW: float, Auto: bool = False) -> None:
        """
        Configures the analyzer Resolution Bandwidth for a swept spectrum measurement.
        Args:
            RBW (float): Resolution bandwidth in Hz.
            Auto (bool): default=False Whether to use automatic RBW setting.
            if Auto is True, RBW parameter is ignored and the analyzer will automatically select the RBW.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        if Auto:
            self.write(":SENSe:BWIDth:RESolution:AUTO ON")
        else:
            self.write(f":SENSe:BWIDth:RESolution {RBW}")

        self.err_check()

    def config_SA_SweepSpectrum_VBW(self, VBW: float, Auto: bool = False) -> None:
        """
        Configures the analyzer Video Bandwidth for a swept spectrum measurement.
        Args:
            VBW (float): Video bandwidth in Hz.
            Auto (bool): default=False Whether to use automatic VBW setting.
            if Auto is True, VBW parameter is ignored and the analyzer will automatically select the VBW.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        if Auto:
            self.write(":SENSe:BWIDth:VIDeo:AUTO ON")
        else:
            self.write(f":SENSe:BWIDth:VIDeo {VBW}")

        self.err_check()

    def config_SA_SweepSpectrum_averageCount(self, averageCount: int, averageType: str = "Auto", On: bool = True) -> None:
        """
        Configures the analyzer average count for a swept spectrum measurement.
        Args:
            averageCount (int): Average count.
            averageType (str): The type of averaging to use. Options: "LOG", "RMS", "SCALar", "Auto".
            On (bool): default=True Whether to turn on averaging.
            if On is False, the analyzer will not average the measurements.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        if On:
            self.write(":SENSe:AVERage:STATe On")
            self.write(f":SENSe:AVERage:COUNt {averageCount}")
        else:
            self.write(":SENSe:AVERage:STATe Off")

        if averageType not in ["LOG", "RMS", "SCALar", "Auto"]:
            raise ValueError("Invalid averageType. Options: 'LOG', 'RMS', 'SCALar', 'Auto'.")   
        else:
            if averageType != "Auto":
                self.write(":SENSe:AVERage:TYPE:AUTO 0")
                self.write(f":SENSe:AVERage:TYPE {averageType}")   
            else:
                self.write(":SENSe:AVERage:TYPE:AUTO 1")

        self.err_check()

    def config_SA_SweepSpectrum_detectorType(self, detectorType: str, trace_number: int = 1) -> None:
        """
        Configures the analyzer detector type for a swept spectrum measurement.
        Args:
            detectorType (str): Detector type. Options: "NORMal", "AVERage", "POSitive", "SAMPle", "NEGative".
            trace_number (int): The trace number for which to configure the detector type.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        valid_detectors = ["NORMal", "AVERage", "POSitive", "SAMPle", "NEGative"]
        if detectorType not in valid_detectors:
            raise ValueError(f"Invalid detector type. Valid options are: {valid_detectors}")

        self.write(f":SENSe:DETector:TRACe{trace_number} {detectorType}")

        self.err_check()    

    def config_SA_SweepSpectrum_sweepTime(self, sweepTime: float, Auto: bool = False) -> None:
        """
        Configures the analyzer sweep time for a swept spectrum measurement.
        Args:
            sweepTime (float): Sweep time in seconds.
            Auto (bool): default=False Whether to use automatic sweep time setting.
            if Auto is True, sweepTime parameter is ignored and the analyzer will automatically select the sweep time.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        if Auto:
            self.write(":SENSe:SWEep:TIME:AUTO ON")
        else:
            self.write(f":SENSe:SWEep:TIME {sweepTime}")

        self.err_check()

    def config_SA_SweepSpectrum_externalGain(self, externalGain: float) -> None:
        """
        Configures the analyzer external gain for a swept spectrum measurement.
        Args:
            externalGain (float): External gain in dB.
        """
        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write(f":SENSe:CORRection:SA:RF:GAIN {externalGain}")

        self.err_check()    

    def set_SA_CHP_measurement(self, center_frequency: float, span: float, channel_bandwidth: float) -> None:
        """
        Configures the analyzer for a channel power (CHP) measurement.
        Args:
            center_frequency (float): Center frequency in Hz.
            span (float): Analyzer display span in Hz.
            channel_bandwidth (float): Channel bandwidth in Hz.
        """

        if not isinstance(center_frequency, (int, float)) or center_frequency <= 0:
            raise ValueError("center_frequency must be a positive number.")
        if not isinstance(span, (int, float)) or span <= 0:
            raise ValueError("span must be a positive number.")
        if not isinstance(channel_bandwidth, (int, float)) or channel_bandwidth <= 0:
            raise ValueError("channel_bandwidth must be a positive number.")

        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write("configure:chpower")
        
        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f":SENSe:CHPower:BANDwidth:INTegration {channel_bandwidth}")
        self.write(f":SENSe:CHPower:FREQuency:SPAN {span}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def query_SA_CHP(self) -> dict[str, float]:
        """
        Triggers a single sweep and returns the channel power (CHP) results.
        Requires set_SA_CHP_measurement() to have been called first.
        Returns:
            dict with keys:
                channelPower (float): Channel power in dBm.
                carrierFreq (float): Measured carrier frequency in Hz.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        
        data = self.query(":FETCh:CHPower1?").strip().split(",")
        channel_power = float(data[0])
        Power_Spectral_Density = float(data[1])

        self.err_check()

        return {"channelPower": channel_power, "powerSpectralDensity": Power_Spectral_Density}

    def set_SA_OBW_measurement(self, center_frequency: float, span: float) -> None:
        """
        Configures the analyzer for an occupied bandwidth (OBW) measurement.
        Args:
            center_frequency (float): Center frequency in Hz.
            span (float): Analyzer display span in Hz.
        """

        if not isinstance(center_frequency, (int, float)) or center_frequency <= 0:
            raise ValueError("center_frequency must be a positive number.")
        if not isinstance(span, (int, float)) or span <= 0:
            raise ValueError("span must be a positive number.")

        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write("configure:obw")
        
        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f":SENSe:EBWidth:FREQuency:SPAN {span}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def query_SA_OBW(self) -> dict[str, float]:
        """
        Triggers a single sweep and returns the occupied bandwidth (OBW) results.
        Requires set_SA_OBW_measurement() to have been called first.
        Returns:
            dict with keys:
                occupiedBandwidth (float): Occupied bandwidth in Hz.
                carrierFreq (float): Measured carrier frequency in Hz.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        
        data = self.query("fetch:obw?").strip().split(",")
        occupied_bandwidth = float(data[0])
        carrier_power = float(data[1])
        xdB_bandwidth = float(data[6])

        self.err_check()

        return {"occupiedBandwidth": occupied_bandwidth, "carrierPower": carrier_power, "xDBBandwidth": xdB_bandwidth}

    def set_SA_ACP_measurement(self, center_frequency: float, span: float) -> None:
        """
        Configures the analyzer for an adjacent channel power (ACP) measurement.
        Args:
            center_frequency (float): Center frequency in Hz.
            span (float): Analyzer display span in Hz.
        """

        if not isinstance(center_frequency, (int, float)) or center_frequency <= 0:
            raise ValueError("center_frequency must be a positive number.")
        if not isinstance(span, (int, float)) or span <= 0:
            raise ValueError("span must be a positive number.")

        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write("configure:acpower")
        
        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f":SENSe:ACPower:FREQuency:SPAN {span}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()
    
    def config_SA_ACP_carrierOffset(self, integration_BW : float, Offset_Freq: float, carrier_count: int = 1) -> None:
        """
        Configures the analyzer for an adjacent channel power (ACP) measurement.
        Args:
            integration_BW (float): Integration bandwidth in Hz.
            Offset_Freq (float): Offset frequency in Hz.
            carrier_count (int): Number of carriers to measure.
        """

        if not isinstance(integration_BW, (int, float)) or integration_BW <= 0:
            raise ValueError("integration_BW must be a positive number.")
        if not isinstance(Offset_Freq, (int, float)) or Offset_Freq <= 0:
            raise ValueError("Offset_Freq must be a positive number.")

        if self.saMode != "SA":
            self.set_saMode("SA")
        
        self.write(f":SENSe:ACPower:CARRier1:COUNt {carrier_count}")
        self.write(f":SENSe:ACPower:BANDwidth:INTegration {integration_BW}")
        self.write(f":SENSe:ACPower:OFFSet:OUTer:LIST {Offset_Freq}")
        self.write(f":SENSe:ACPower:OFFSet:OUTer:LIST:BANDwidth:INTegration {integration_BW}")

        # self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def query_SA_ACP(self) -> dict[str, float]:
        """
        Triggers a single sweep and returns the adjacent channel power (ACP) results.
        Requires set_SA_ACP_measurement() to have been called first.
        Returns:
            dict with keys:
                adjacentChannelPower (float): Adjacent channel power in dBm.
                carrierFreq (float): Measured carrier frequency in Hz.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        
        data = self.query(":FETCh:ACPower?").strip().split(',')

        total_carrier_power = float(data[0])
        adjacent_channel_power_lower = float(data[1])
        adjacent_channel_power_upper = float(data[2])

        self.err_check()

        return {"totalCarrierPower": total_carrier_power, "acpLower": adjacent_channel_power_lower, "acpUpper": adjacent_channel_power_upper}

    def set_SA_CCDF_measurement(self, center_frequency: float, info_bw: float) -> None:
        """
        Configures the analyzer for a complementary cumulative distribution function (CCDF) measurement.
        Args:
            center_frequency (float): Center frequency in Hz.
            info_bw (float): Information bandwidth in Hz.
        """

        if not isinstance(center_frequency, (int, float)) or center_frequency <= 0:
            raise ValueError("center_frequency must be a positive number.")
        if not isinstance(info_bw, (int, float)) or info_bw <= 0:
            raise ValueError("info_bw must be a positive number.")

        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write(":CONFigure:PSTatistic")
        
        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f":SENSe:PSTatistic:BANDwidth {info_bw}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def query_SA_CCDF(self) -> dict[str, Any]:
        """
        Triggers a single sweep and returns the complementary cumulative distribution function (CCDF) results.
        Requires set_SA_CCDF_measurement() to have been called first.
        Returns:
            dict with keys:
                ccdfData (np.ndarray): CCDF data points.
                carrierFreq (float): Measured carrier frequency in Hz.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        
        ccdf_data = np.array([float(v) for v in self.query(":FETCh:PSTatistic?").strip().split(",")])


        self.err_check()

        return {"ccdfData": ccdf_data}

    def set_SA_BurstPower_measurement(self, center_frequency: float, sweep_time: float) -> None:
        """
        Configures the analyzer for a burst power measurement.
        Args:
            center_frequency (float): Center frequency in Hz.
            sweep_time (float): Sweep time in seconds.
        """

        if not isinstance(center_frequency, (int, float)) or center_frequency <= 0:
            raise ValueError("center_frequency must be a positive number.")
        if not isinstance(sweep_time, (int, float)) or sweep_time <= 0:
            raise ValueError("sweep_time must be a positive number.")

        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write("CONFigure:TXPower")
        
        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f":SENSe:TXPower:SWEep:TIME {sweep_time}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def query_SA_BurstPower(self) -> dict[str, float]:
        """
        Triggers a single sweep and returns the burst power results.
        Requires set_SA_BurstPower_measurement() to have been called first.
        Returns:
            dict with keys:
                burstPower (float): Burst power in dBm.
                carrierFreq (float): Measured carrier frequency in Hz.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        
        data = self.query(":FETCh:TXPower?").strip().split(",")
        burst_power = float(data[2])
        output_power = float(data[1])
        self.err_check()

        return {"burstPower": burst_power, "outputPower": output_power}
        """
        Triggers a single sweep and returns the spectrum emission mask (SEM) results.
        Requires set_SA_SEM_measurement() to have been called first.
        Returns:
            dict with keys:
                semLevel (float): Spectrum emission mask level in dBm.
                carrierFreq (float): Measured carrier frequency in Hz.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        
        sem_level = float(self.query("fetch:sem1?").strip())
        carrier_freq = float(self.query("fetch:sem2?").strip())

        self.err_check()

        return {"semLevel": sem_level, "carrierFreq": carrier_freq}

    def set_SA_TOI_measurement(self, center_frequency: float, span: float) -> None:
        """
        Configures the analyzer for a third-order intercept (TOI) measurement.
        Args:
            center_frequency (float): Center frequency in Hz.
            span (float): Analyzer display span in Hz.
        """

        if not isinstance(center_frequency, (int, float)) or center_frequency <= 0:
            raise ValueError("center_frequency must be a positive number.")
        if not isinstance(span, (int, float)) or span <= 0:
            raise ValueError("span must be a positive number.")

        if self.saMode != "SA":
            self.set_saMode("SA")

        self.write("configure:toi")
        
        self.write(f"sense:frequency:center {center_frequency}")
        self.write(f":SENSe:TOI:FREQuency:SPAN {span}")

        self.cf = float(self.query("sense:frequency:center?").strip())
        self.err_check()

    def query_SA_TOI(self) -> dict[str, float]:
        """
        Triggers a single sweep and returns the third-order intercept (TOI) results.
        Requires set_SA_TOI_measurement() to have been called first.
        Returns:
            dict with keys:
                toi (float): Third-order intercept point in dBm.
                lower_3rd_Freq (float): Lower third-order frequency in Hz.
                lower_3rd_Amp (float): Lower third-order amplitude in dBm.
                lower_3rd_toi (float): Lower third-order intercept in dBm.
                lower_tone_Freq (float): Lower tone frequency in Hz.
                lower_tone_Amp (float): Lower tone amplitude in dBm.
                upper_tone_Freq (float): Upper tone frequency in Hz.
                upper_tone_Amp (float): Upper tone amplitude in dBm.
                upper_3rd_Freq (float): Upper third-order frequency in Hz.
                upper_3rd_Amp (float): Upper third-order amplitude in dBm.
                upper_3rd_toi (float): Upper third-order intercept in dBm.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")

        data = self.query("fetch:toi2?").strip().split(',')
        
        toi = float(data[9])
        lower_3rd_Freq = float(data[0])
        lower_3rd_Amp = float(data[1])
        lower_3rd_toi = float(data[2])
        lower_tone_Freq = float(data[3])
        lower_tone_Amp = float(data[4])
        upper_tone_Freq = float(data[5])
        upper_tone_Amp = float(data[6])
        upper_3rd_Freq = float(data[10])
        upper_3rd_Amp = float(data[11])
        upper_3rd_toi = float(data[12])
   
        self.err_check()

        return {"toi": toi, "lower3rdFreq": lower_3rd_Freq, "lower3rdAmp": lower_3rd_Amp, "lower3rdToi": lower_3rd_toi, "lowerToneFreq": lower_tone_Freq, "lowerToneAmp": lower_tone_Amp, "upperToneFreq": upper_tone_Freq, "upperToneAmp": upper_tone_Amp, "upper3rdFreq": upper_3rd_Freq, "upper3rdAmp": upper_3rd_Amp, "upper3rdToi": upper_3rd_toi}

        """
        Triggers a single sweep and returns the power vs. time (PAvT) results.
        Requires set_SA_PAvT_measurement() to have been called first.
        Returns:
            dict with keys:
                pavtLevel (float): Power vs. time level in dBm.
                carrierFreq (float): Measured carrier frequency in Hz.
        """

        self.write("initiate:continuous off")
        
        self.write("initiate:immediate")
        self.query("*OPC?")
        
        pavt_level = float(self.query("fetch:pavt1?").strip())
        carrier_freq = float(self.query("fetch:pavt2?").strip())

        self.err_check()

        return {"pavtLevel": pavt_level, "carrierFreq": carrier_freq}



class PXA(UXA):
    """
    Class for controlling PXA-family X-series signal analyzers (N9030B, N9032B).
    The SCPI command set used by this driver is shared across X-series signal
    analyzers, so all measurements are inherited from UXA unchanged.
    Verified on N9032B.
    """

    pass
