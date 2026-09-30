"""
Copyright © Keysight Technologies 2026

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

import os
from time import sleep
import numpy as np
from datetime import datetime
import math

import csvResult

import KeysightSgDriver
import KeysightSaDriver





vsg_address = ""
sa_address = ""

#1CC files
vsg_waveform_file=""
vsg_fs = 1e9
sa_ofdm_state_file=""
sa_acp_state_file = ""
numCarriers = 1
num_acp_offsets = 1




FREQUENCY_START = 2000000000
FREQUENCY_STOP = 3000000000
FREQUENCY_STEP = 100000000


FREQUENCY_HZ = [4000000000, 5000000000]

SG_POWER_MAX = 0
SG_POWER_START = -10
SG_POWER_STOP = 0
SG_POWER_STEP = 1
SG_POWER_DBM = np.arange(SG_POWER_START, SG_POWER_STOP + (SG_POWER_STEP/2), SG_POWER_STEP)


# measurement functions
evm_bathtub = False
acp = False

sa_chp = False
sa_obw = False
sa_acp = False
sa_ccdf = False
sa_burst_power = False
sa_toi = False
sa_sweep_spectrum = True


# Connect to SG and SA
vsg = KeysightSgDriver.VXG(vsg_address,"pyvisa",10,protocol="hislip",port=0)
sa = KeysightSaDriver.UXA(sa_address,"pyvisa",30,protocol="hislip", port=0)
print(f"Connected to SA at address {sa_address} and SG at address {vsg_address}")


# Build csv file name and open the file
#print(os.getcwd())
csvFileName = f"{os.getcwd()}\\Results\\EVM_Bathtub_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
# csvFileName = f"{os.getcwd()}\\KeysightSaSgDriver\\Results\\EVM_Bathtub_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

resultID = csvResult.open_csv(csvFileName)

# Write cvs result file field name header
csvResult.write_header(resultID, "CC,Frequency,Tx Power,Rx Power,EVM (%),EVM(dB)")

# Download waveform from local PC to SG 
vsg.download_wfm(wfmID = vsg_waveform_file, localPath = f"{os.getcwd()}\\Waveform")
# vsg.download_wfm(wfmID = vsg_waveform_file, localPath = f"{os.getcwd()}\\KeysightSaSgDriver\\Waveform")

# download state filr from local PC to SA 
stateFileList = sa.get_state_files()
print(f"Current state files in SA: {stateFileList}")

# check if state file exists then transfer if not
if sa_ofdm_state_file not in stateFileList:
    print(f"State file {sa_ofdm_state_file} not found in SA. Transferring file...")
    sa.transfer_file(f"{os.getcwd()}\\SA State Files\\{sa_ofdm_state_file}")
    # sa.transfer_file(f"{os.getcwd()}\\KeysightSaSgDriver\\SA State Files\\{sa_ofdm_state_file}")

if sa_acp_state_file not in stateFileList:
    print(f"State file {sa_acp_state_file} not found in SA. Transferring file...")
    sa.transfer_file(f"{os.getcwd()}\\SA State Files\\{sa_acp_state_file}")
    # sa.transfer_file(f"{os.getcwd()}\\KeysightSaSgDriver\\SA State Files\\{sa_acp_state_file}")

if acp:
    print("Running ACP test...")
   
    sa.recall_state(f"D:\\users\\instrument\\documents\\vma\\state\\{sa_acp_state_file}")
   
    sa.set_continuous_measurement(False)

    for test_freq in FREQUENCY_HZ:

        vsg.set_fs(float(vsg_fs),ch=1)
        vsg.set_cf(float(test_freq),ch=1)
        # Stop source waveform playing
        vsg.stop(ch=1)
        # set SG putput power
        vsg.set_amp(SG_POWER_MAX, ch=1)
        # turn on SG and play waveform
        vsg.play(wfmID=vsg_waveform_file,ch=1)



        sa.set_cf(float(test_freq))
    
        acp_result = sa.query_ACP_table()

        total_carrier_power = acp_result["totalCarrierPower"]
        ref_carrier_power = acp_result["refCarrierPower"]
        offsets = acp_result["offsets"]
        

        print(f"total power: {total_carrier_power}, reference carrier power: {ref_carrier_power}")


        for offset in range(num_acp_offsets):
            current_offset = offsets[offset]
            lower_offset = current_offset["relLower"]
            upper_offset = current_offset["relUpper"]
            print(f"offset #{offset} lower: {lower_offset}, upper: {upper_offset}")

if evm_bathtub:
    print("Running EVM bathtub curve test...")
    
    sa.recall_state(f"D:\\users\\instrument\\documents\\vma\\state\\{sa_ofdm_state_file}")
   
 
    for test_freq in FREQUENCY_HZ:
        # config waveform sampling rate amd center frequency
        vsg.set_fs(float(vsg_fs),ch=1)
        vsg.set_cf(float(test_freq),ch=1)

    
        # setup SA for testing 
        sa.set_cf(float(test_freq));


        for test_power in SG_POWER_DBM:
            # Stop source waveform playing
            vsg.stop(ch=1)
            # set SG putput power
            vsg.set_amp(test_power, ch=1)
            # turn on SG and play waveform
            vsg.play(wfmID=vsg_waveform_file,ch=1)
        

            # optimize EVM
            sa.optimize_evm()
           
            # fetch power and EVM for each carrier
            evmResult = sa.query_evm(numCarriers)
           
            # write results to file
            for carrier in range(numCarriers):
                # Convert evm % to evm dB
                rxPower = evmResult[2*carrier]
                evm = evmResult[2*carrier+1]
                evm_db = 20 * math.log10(abs(evm) / 100) #added abs() function to catch error where EVM is 0
                
                print(f"Carrier: {carrier}, Freq: {test_freq} Hz,Tx Power: {test_power} dBm, Rx Power: {rxPower}, EVM(%): {evm} %, EVM(dB): {evm_db} dB")
                # write result to csv file
                csvResult.write_row(resultID, f"{carrier},{test_freq},{test_power},{rxPower},{evm},{evm_db}")
                
          
            

    # close cvs file
    csvResult.close_csv(resultID)

if sa_chp:
    print("Running CHP test...")

    span = "1000e6"  # Example span value
    test_freq = "5000e6"  # Example test frequency value
    test_power = "-10"  # Example test power value
    channel_bandwidth = "100e6"  # Example channel bandwidth value


    # config waveform sampling rate amd center frequency
    vsg.set_fs(float(vsg_fs),ch=1)
    vsg.set_cf(float(test_freq),ch=1)
    
    # Stop source waveform playing
    vsg.stop(ch=1)
    # set SG putput power
    vsg.set_amp(test_power, ch=1)
    # turn on SG and play waveform
    vsg.play(wfmID=vsg_waveform_file,ch=1)

    sa.set_SA_CHP_measurement(float(test_freq), float(span), float(channel_bandwidth))
    sa.optimize_SA_Attenuation()
    chp_result = sa.query_SA_CHP()

    print(f"Channel Power: {chp_result['channelPower']} dBm/{float(channel_bandwidth)/1e6}MHz")
    print(f"Power Spectral Density: {chp_result['powerSpectralDensity']} dBm/Hz")

if sa_obw:
    print("Running OBW test...")

    span = "1000e6"  # Example span value
    test_freq = "5000e6"  # Example test frequency value
    test_power = "-10"  # Example test power value
    channel_bandwidth = "100e6"  # Example channel bandwidth value


    # config waveform sampling rate amd center frequency
    vsg.set_fs(float(vsg_fs),ch=1)
    vsg.set_cf(float(test_freq),ch=1)
    
    # Stop source waveform playing
    vsg.stop(ch=1)
    # set SG putput power
    vsg.set_amp(test_power, ch=1)
    # turn on SG and play waveform
    vsg.play(wfmID=vsg_waveform_file,ch=1)

    sa.set_SA_OBW_measurement(float(test_freq), float(span))
    sa.optimize_SA_Attenuation()
    obw_result = sa.query_SA_OBW()

    print(f"Occupied Bandwidth: {obw_result['occupiedBandwidth']} Hz")
    print(f"Carrier Power: {obw_result['carrierPower']} dBm")
    print(f"xdB Bandwidth: {obw_result['xDBBandwidth']} Hz")

if sa_acp:
    print("Running ACP test...")

    span = "1000e6"  # Example span value
    test_freq = "5000e6"  # Example test frequency value
    test_power = "-10"  # Example test power value
    channel_bandwidth = "100e6"  # Example channel bandwidth value
    carrier_count = 1  # Example carrier count value
    Offset_Freq = "100e6"  # Example offset frequency value

    # config waveform sampling rate amd center frequency
    vsg.set_fs(float(vsg_fs),ch=1)
    vsg.set_cf(float(test_freq),ch=1)
    
    # Stop source waveform playing
    vsg.stop(ch=1)
    # set SG putput power
    vsg.set_amp(test_power, ch=1)
    # turn on SG and play waveform
    vsg.play(wfmID=vsg_waveform_file,ch=1)

    sa.set_SA_ACP_measurement(float(test_freq), float(span))
    sa.config_SA_ACP_carrierOffset(float(channel_bandwidth), float(Offset_Freq), int(carrier_count))
    sa.optimize_SA_Attenuation()
    acp_result = sa.query_SA_ACP()

    print(f"Total Carrier Power: {acp_result['totalCarrierPower']} dBm")
    print(f"Adjacent Channel Power Lower: {acp_result['acpLower']} dBc")
    print(f"Adjacent Channel Power Upper: {acp_result['acpUpper']} dBc")

if sa_ccdf:
    print("Running CCDF test...")

    span = "1000e6"  # Example span value
    test_freq = "5000e6"  # Example test frequency value
    test_power = "-10"  # Example test power value
    channel_bandwidth = "100e6"  # Example channel bandwidth value
    carrier_count = 1  # Example carrier count value
    Offset_Freq = "100e6"  # Example offset frequency value

    # config waveform sampling rate amd center frequency
    vsg.set_fs(float(vsg_fs),ch=1)
    vsg.set_cf(float(test_freq),ch=1)
    
    # Stop source waveform playing
    vsg.stop(ch=1)
    # set SG putput power
    vsg.set_amp(test_power, ch=1)
    # turn on SG and play waveform
    vsg.play(wfmID=vsg_waveform_file,ch=1)

    sa.set_SA_CCDF_measurement(float(test_freq), float(channel_bandwidth))
    sa.optimize_SA_Attenuation()
    ccdf_result = sa.query_SA_CCDF()

    print(f"CCDF Data: {ccdf_result['ccdfData']}")

if sa_burst_power:
    print("Running Burst Power test...")

    span = "1000e6"  # Example span value
    test_freq = "5000e6"  # Example test frequency value
    test_power = "-10"  # Example test power value
    sweep_time = "0.001"  # Example sweep time value
    carrier_count = 1  # Example carrier count value
    Offset_Freq = "100e6"  # Example offset frequency value

    # config waveform sampling rate amd center frequency
    vsg.set_fs(float(vsg_fs),ch=1)
    vsg.set_cf(float(test_freq),ch=1)
    
    # Stop source waveform playing
    vsg.stop(ch=1)
    # set SG putput power
    vsg.set_amp(test_power, ch=1)
    # turn on SG and play waveform
    vsg.play(wfmID=vsg_waveform_file,ch=1)

    sa.set_SA_BurstPower_measurement(float(test_freq), float(sweep_time))
    sa.optimize_SA_Attenuation()
    sa.set_SA_trigger("TXP", "IMM", 0)
    burst_power_result = sa.query_SA_BurstPower()

    print(f"Burst Power Data above threshold: {burst_power_result['burstPower']} dBm")
    print(f"Output Power Data: {burst_power_result['outputPower']} dBm")

if sa_toi:
    print("Running TOI test...")

    span = "50e6"  # Example span value
    test_freq = "5000e6"  # Example test frequency value
    test_power = "-10"  # Example test power value
    channel_bandwidth = "100e6"  # Example channel bandwidth value
    carrier_count = 1  # Example carrier count value
    Offset_Freq = "100e6"  # Example offset frequency value

    # config waveform sampling rate amd center frequency
    # vsg.set_fs(float(vsg_fs),ch=1)
    vsg.set_cf(float(test_freq),ch=1)
    
    # Stop source waveform playing
    vsg.stop(ch=1)

    # set up VXG multi-tone mpde to generate two tone
    vsg.set_SG_mode("MTON", ch=1)
    vsg.configure_multitone(tones=2, Tone_space=10e6, ch=1)
    # set SG putput power
    vsg.set_amp(test_power, ch=1)

    sa.set_SA_TOI_measurement(float(test_freq), float(span))
    sa.optimize_SA_Attenuation()
    toi_result = sa.query_SA_TOI()

    # switch back to waveform mode after TOI measurement
    vsg.stop(ch=1)
    vsg.set_SG_mode("WAV", ch=1)

    print(f"TOI Data: {toi_result['toi']} dBm")
    print(f"Lower 3rd Order Data: {toi_result['lower3rdFreq']} Hz, {toi_result['lower3rdAmp']} dBm, {toi_result['lower3rdToi']} dBm")
    print(f"Lower Tone Data: {toi_result['lowerToneFreq']} Hz, {toi_result['lowerToneAmp']} dBm")
    print(f"Upper Tone Data: {toi_result['upperToneFreq']} Hz, {toi_result['upperToneAmp']} dBm")
    print(f"Upper 3rd Order Data: {toi_result['upper3rdFreq']} Hz, {toi_result['upper3rdAmp']} dBm, {toi_result['upper3rdToi']} dBm")

if sa_sweep_spectrum:
    print("Running Swept Spectrum test...")

    span = "100e6"  # Example span value
    test_freq = "5000e6"  # Example test frequency value
    test_power = "-10"  # Example test power value

    # config waveform sampling rate amd center frequency
    vsg.set_fs(float(vsg_fs),ch=1)
    vsg.set_cf(float(test_freq),ch=1)
    
    # Stop source waveform playing
    vsg.stop(ch=1)
    # set SG putput power
    vsg.set_amp(test_power, ch=1)

    # turn on SG and play waveform
    vsg.play(wfmID=vsg_waveform_file,ch=1)

    sa.set_SA_SweepSpectrum_measurement(float(test_freq), float(span))

    sa.config_SA_SweepSpectrum_RBW(1e6)
    sa.config_SA_SweepSpectrum_VBW(1e6)
    sa.config_SA_SweepSpectrum_averageCount(10, "RMS")
    sa.config_SA_SweepSpectrum_detectorType("AVERage")
    sa.config_SA_SweepSpectrum_sweepTime(0.1)
    sa.config_SA_SweepSpectrum_externalGain(0)

    sa.optimize_SA_Attenuation()
    sa.set_continuous_measurement(True)
