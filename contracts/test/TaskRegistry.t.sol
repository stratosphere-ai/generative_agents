// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {TaskRegistry} from "../src/TaskRegistry.sol";

contract TaskRegistryTest is Test {
    TaskRegistry registry;
    address agent  = address(0xA11CE);
    address other  = address(0xB0B);

    function setUp() public {
        registry = new TaskRegistry();
    }

    function test_StartTask_AssignsMonotonicId() public {
        vm.prank(agent);
        uint256 id1 = registry.startTask(0, keccak256("HOURLY"), keccak256("desc-1"), keccak256("spo-1"), "");
        vm.prank(agent);
        uint256 id2 = registry.startTask(0, keccak256("HOURLY"), keccak256("desc-2"), keccak256("spo-2"), "");
        assertEq(id1, 1);
        assertEq(id2, 2);
    }

    function test_StartTask_LinksParentChild() public {
        vm.prank(agent);
        uint256 parentId = registry.startTask(0, keccak256("HOURLY"), keccak256("p"), keccak256("p-spo"), "");
        vm.prank(agent);
        uint256 childId = registry.startTask(parentId, keccak256("ACTION"), keccak256("c"), keccak256("c-spo"), "");

        TaskRegistry.Task memory child = registry.getTask(childId);
        assertEq(child.parentId, parentId);
    }

    function test_StartTask_RevertsOnUnknownParent() public {
        vm.prank(agent);
        vm.expectRevert(abi.encodeWithSelector(TaskRegistry.ParentNotFound.selector, uint256(999)));
        registry.startTask(999, keccak256("ACTION"), keccak256("c"), keccak256("c-spo"), "");
    }

    function test_CompleteTask_OnlyByAgent() public {
        vm.prank(agent);
        uint256 id = registry.startTask(0, keccak256("ACTION"), keccak256("d"), keccak256("spo"), "");

        vm.prank(other);
        vm.expectRevert(abi.encodeWithSelector(TaskRegistry.NotTaskAgent.selector, id, other));
        registry.completeTask(id, keccak256("result"), "");

        vm.prank(agent);
        registry.completeTask(id, keccak256("result"), "");

        TaskRegistry.Task memory t = registry.getTask(id);
        assertEq(uint8(t.status), uint8(TaskRegistry.Status.Completed));
        assertEq(t.resultHash, keccak256("result"));
    }

    function test_CompleteTask_RevertsIfNotStarted() public {
        vm.prank(agent);
        uint256 id = registry.startTask(0, keccak256("ACTION"), keccak256("d"), keccak256("spo"), "");
        vm.prank(agent);
        registry.completeTask(id, keccak256("r"), "");

        vm.prank(agent);
        vm.expectRevert();
        registry.completeTask(id, keccak256("r2"), "");
    }

    function test_FailTask() public {
        vm.prank(agent);
        uint256 id = registry.startTask(0, keccak256("ACTION"), keccak256("d"), keccak256("spo"), "");
        vm.prank(agent);
        registry.failTask(id, keccak256("oom"));

        TaskRegistry.Task memory t = registry.getTask(id);
        assertEq(uint8(t.status), uint8(TaskRegistry.Status.Failed));
    }

    function test_GetTask_RevertsOnUnknown() public {
        vm.expectRevert(abi.encodeWithSelector(TaskRegistry.TaskNotFound.selector, uint256(42)));
        registry.getTask(42);
    }
}
