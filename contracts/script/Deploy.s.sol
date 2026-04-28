// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console2} from "forge-std/Script.sol";
import {TaskRegistry} from "../src/TaskRegistry.sol";

contract Deploy is Script {
    function run() external returns (TaskRegistry registry) {
        uint256 pk = vm.envUint("PRIVATE_KEY");
        vm.startBroadcast(pk);
        registry = new TaskRegistry();
        vm.stopBroadcast();

        console2.log("TaskRegistry deployed at:", address(registry));
    }
}
