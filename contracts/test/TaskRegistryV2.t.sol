// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {TaskRegistryV2} from "../src/TaskRegistryV2.sol";

contract TaskRegistryV2Test is Test {
    TaskRegistryV2 registry;
    address agent  = address(0xA11CE);
    address other  = address(0xB0B);

    function setUp() public {
        registry = new TaskRegistryV2();
    }

    // ---- V1 surface still works on V2 -------------------------------------

    function test_V1_StartCompleteRoundTrip() public {
        vm.prank(agent);
        uint256 id = registry.startTask(0, keccak256("HOURLY"), keccak256("d"), keccak256("spo"), "");
        vm.prank(agent);
        registry.completeTask(id, keccak256("r"), "");
        TaskRegistryV2.Task memory t = registry.getTask(id);
        assertEq(uint8(t.status), uint8(TaskRegistryV2.Status.Completed));
    }

    function test_V1_StartTask_RejectsUnknownParent() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(TaskRegistryV2.ParentNotFound.selector, uint256(999)));
        registry.startTask(999, keccak256("ACTION"), keccak256("c"), keccak256("c-spo"), "");
    }

    function test_V1_CompleteTask_OnlyByAgent() public {
        vm.prank(agent);
        uint256 id = registry.startTask(0, keccak256("ACTION"), keccak256("d"), keccak256("spo"), "");
        vm.prank(other);
        vm.expectRevert(abi.encodeWithSelector(TaskRegistryV2.NotTaskAgent.selector, id, other));
        registry.completeTask(id, keccak256("r"), "");
    }

    // ---- V2 batch start ---------------------------------------------------

    function test_StartTaskBatch_AssignsMonotonicIds() public {
        TaskRegistryV2.StartArgs[] memory items = new TaskRegistryV2.StartArgs[](3);
        for (uint256 i = 0; i < 3; ++i) {
            items[i] = TaskRegistryV2.StartArgs({
                parentId:        0,
                taskType:        keccak256("ACTION"),
                descriptionHash: keccak256(abi.encodePacked("d", i)),
                eventSPO:        keccak256(abi.encodePacked("spo", i)),
                cid:             ""
            });
        }
        vm.prank(agent);
        uint256[] memory ids = registry.startTaskBatch(items);
        assertEq(ids[0], 1);
        assertEq(ids[1], 2);
        assertEq(ids[2], 3);
        // All assigned to the calling agent.
        TaskRegistryV2.Task memory t = registry.getTask(ids[2]);
        assertEq(t.agent, agent);
    }

    function test_StartTaskBatch_RevertsOnEmpty() public {
        TaskRegistryV2.StartArgs[] memory items = new TaskRegistryV2.StartArgs[](0);
        vm.prank(agent);
        vm.expectRevert(TaskRegistryV2.EmptyBatch.selector);
        registry.startTaskBatch(items);
    }

    function test_StartTaskBatch_AtomicallyRevertsOnUnknownParent() public {
        TaskRegistryV2.StartArgs[] memory items = new TaskRegistryV2.StartArgs[](2);
        items[0] = TaskRegistryV2.StartArgs(0, keccak256("ACTION"), keccak256("a"), keccak256("a"), "");
        items[1] = TaskRegistryV2.StartArgs(999, keccak256("ACTION"), keccak256("b"), keccak256("b"), "");
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(TaskRegistryV2.ParentNotFound.selector, uint256(999)));
        registry.startTaskBatch(items);
        // No partial state: nextId stayed at 1.
        assertEq(registry.nextId(), 1);
    }

    // ---- V2 batch complete ------------------------------------------------

    function test_CompleteTaskBatch_FlipsStatus() public {
        // First start three tasks.
        TaskRegistryV2.StartArgs[] memory starts = new TaskRegistryV2.StartArgs[](3);
        for (uint256 i = 0; i < 3; ++i) {
            starts[i] = TaskRegistryV2.StartArgs(0, keccak256("ACTION"),
                keccak256(abi.encodePacked("d", i)), keccak256(abi.encodePacked("spo", i)), "");
        }
        vm.prank(agent);
        uint256[] memory ids = registry.startTaskBatch(starts);

        TaskRegistryV2.CompleteArgs[] memory completes = new TaskRegistryV2.CompleteArgs[](3);
        for (uint256 i = 0; i < 3; ++i) {
            completes[i] = TaskRegistryV2.CompleteArgs({
                id:         ids[i],
                resultHash: keccak256(abi.encodePacked("r", i)),
                cid:        ""
            });
        }
        vm.prank(agent);
        registry.completeTaskBatch(completes);

        TaskRegistryV2.Task memory t = registry.getTask(ids[1]);
        assertEq(uint8(t.status), uint8(TaskRegistryV2.Status.Completed));
        assertEq(t.resultHash, keccak256(abi.encodePacked("r", uint256(1))));
    }

    // ---- V2 atomic action -------------------------------------------------

    function test_RecordAtomicAction_StatusCompletedInOneCall() public {
        vm.prank(agent);
        uint256 id = registry.recordAtomicAction(
            0, keccak256("ACTION"), keccak256("sold-coke"), keccak256("vendy|sold|coke"),
            keccak256("price=150"), ""
        );
        TaskRegistryV2.Task memory t = registry.getTask(id);
        assertEq(uint8(t.status), uint8(TaskRegistryV2.Status.Completed));
        assertEq(t.endTime,       t.startTime);
        assertEq(t.resultHash,    keccak256("price=150"));
    }

    function test_RecordAtomicActionBatch_Parallel() public {
        TaskRegistryV2.StartArgs[] memory starts = new TaskRegistryV2.StartArgs[](2);
        starts[0] = TaskRegistryV2.StartArgs(0, keccak256("ACTION"), keccak256("a"), keccak256("a"), "");
        starts[1] = TaskRegistryV2.StartArgs(0, keccak256("ACTION"), keccak256("b"), keccak256("b"), "");
        bytes32[] memory results = new bytes32[](2);
        results[0] = keccak256("ra");
        results[1] = keccak256("rb");

        vm.prank(agent);
        uint256[] memory ids = registry.recordAtomicActionBatch(starts, results);
        assertEq(ids.length, 2);
        TaskRegistryV2.Task memory t1 = registry.getTask(ids[1]);
        assertEq(uint8(t1.status), uint8(TaskRegistryV2.Status.Completed));
        assertEq(t1.resultHash,    keccak256("rb"));
    }

    function test_RecordAtomicActionBatch_RejectsLengthMismatch() public {
        TaskRegistryV2.StartArgs[] memory starts = new TaskRegistryV2.StartArgs[](2);
        starts[0] = TaskRegistryV2.StartArgs(0, keccak256("ACTION"), keccak256("a"), keccak256("a"), "");
        starts[1] = TaskRegistryV2.StartArgs(0, keccak256("ACTION"), keccak256("b"), keccak256("b"), "");
        bytes32[] memory results = new bytes32[](1);
        results[0] = keccak256("ra");

        vm.prank(agent);
        vm.expectRevert(bytes("resultHashes length mismatch"));
        registry.recordAtomicActionBatch(starts, results);
    }
}
