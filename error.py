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
