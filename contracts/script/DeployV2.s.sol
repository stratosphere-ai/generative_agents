// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TaskRegistryV2} from "../src/TaskRegistryV2.sol";

contract DeployV2 is Script {
    function run() external returns (TaskRegistryV2 registry) {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        vm.startBroadcast(pk);
        registry = new TaskRegistryV2();
        vm.stopBroadcast();

        console2.log("TaskRegistryV2 deployed at:", address(registry));
    }
}
