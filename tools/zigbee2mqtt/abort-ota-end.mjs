// Zigbee2MQTT external extension: answers a TRVZB's UpgradeEndRequest for one
// file version with a ZCL Default Response ABORT (0x95).
//
// Every release built on the stock 1.4.4 SoC image carries a header version
// that differs from the version built into the SoC application (5124), and
// the SoC's Silicon Labs OTA client re-installs such a stored image on every
// boot. The application MCU has already been updated by the time the SoC sends
// UpgradeEndRequest, so aborting that request loses nothing: the client treats
// the upgrade as server-aborted (upgradeEndResponseParse ->
// downloadAndVerifyFinish(SERVER_ABORTED)) and deletes the image instead of
// re-installing it. See docs/update-and-recovery.md.
//
// Set the two constants below, load it with bridge/request/extension/save
// before starting the update, and remove it once the update has ended.
// Answers at most MAXIMUM_ANSWERS requests, then only logs.
const TARGET_IEEE_ADDRESS = "0x0000000000000000";
const ABORTED_FILE_VERSION = 0x0000;
const GEN_OTA_CLUSTER_ID = 0x0019;
const UPGRADE_END_REQUEST_COMMAND_ID = 0x06;
const ZCL_STATUS_SUCCESS = 0x00;
const ZCL_STATUS_ABORT = 0x95;
const MAXIMUM_ANSWERS = 3;

export default class AbortOtaEnd {
    constructor(zigbee, mqtt, state, publishEntityState, eventBus, enableDisableExtension, restartCallback, addExtension, settings, logger) {
        this.eventBus = eventBus;
        this.logger = logger;
        this.answerCount = 0;
    }

    async start() {
        this.eventBus.onDeviceMessage(this, async (message) => {
            if (message.type !== "commandUpgradeEndRequest" || message.device.ieeeAddr !== TARGET_IEEE_ADDRESS) {
                return;
            }
            const request = message.data;
            const transactionSequenceNumber = message.meta.zclTransactionSequenceNumber;
            this.logger.info(`abort-ota-end: UpgradeEndRequest ${JSON.stringify(request)} tsn ${transactionSequenceNumber}`);
            const isTargetedSuccessfulRequest = request.status === ZCL_STATUS_SUCCESS && request.fileVersion === ABORTED_FILE_VERSION;
            if (!isTargetedSuccessfulRequest || this.answerCount >= MAXIMUM_ANSWERS) {
                this.logger.info(`abort-ota-end: not answering (status ${request.status}, fileVersion ${request.fileVersion}, answered ${this.answerCount})`);
                return;
            }
            try {
                await message.endpoint.defaultResponse(UPGRADE_END_REQUEST_COMMAND_ID, ZCL_STATUS_ABORT, GEN_OTA_CLUSTER_ID, transactionSequenceNumber);
                this.answerCount += 1;
                this.logger.info(`abort-ota-end: sent ABORT (${this.answerCount}/${MAXIMUM_ANSWERS})`);
            } catch (error) {
                this.logger.error(`abort-ota-end: ABORT failed: ${error.message}`);
            }
        });
    }

    async stop() {
        this.eventBus.removeListeners(this);
    }
}
