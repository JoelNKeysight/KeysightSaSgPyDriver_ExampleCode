"""
error
Author: Morgan Allison, Keysight RF/uW Application Engineer
Custom error classes for pyarbtools.
"""


class WfmBuilderError(Exception):
    """Waveform Builder Exception class"""

    pass


class GranularityError(Exception):
    """Waveform Granularity Exception class"""

    pass


class VSAError(Exception):
    """VSA Exception class"""

    pass


class InstrumentError(Exception):
    """General Instrument Exception class"""

    pass


class RemotePathError(InstrumentError):
    """Raised when a file/folder path on the instrument does not exist.

    Subclass of InstrumentError so existing handlers still catch it, while
    callers that care (e.g. an AVT/CTF harness) can catch this specifically
    to create the folder or surface a clearer message to the operator.
    """

    pass
