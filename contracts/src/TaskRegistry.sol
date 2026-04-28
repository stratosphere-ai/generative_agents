// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title TaskRegistry
/// @notice Append-only registry of tasks performed by autonomous agents.
///         Two-tier hierarchy: top-level (parentId == 0) and child tasks.
contract TaskRegistry {
    enum Status { Started, Completed, Failed, Cancelled }

    struct Task {
        uint256 id;
        address agent;
        uint256 parentId;        // 0 if top-level
        bytes32 taskType;        // keccak("HOURLY") / keccak("ACTION") / ...
        bytes32 descriptionHash; // keccak256(utf8 description)
        bytes32 eventSPO;        // keccak(s|p|o)
        uint64  startTime;
        uint64  endTime;
        Status  status;
        bytes32 resultHash;
        string  cid;             // optional off-chain payload pointer (IPFS, etc.)
    }

    event TaskStarted(
        uint256 indexed id,
        address indexed agent,
        uint256 indexed parentId,
        bytes32 taskType,
        bytes32 descriptionHash,
        bytes32 eventSPO,
        uint64  startTime,
        string  cid
    );

    event TaskCompleted(
        uint256 indexed id,
        uint64  endTime,
        bytes32 resultHash,
        string  cid
    );

    event TaskFailed(
        uint256 indexed id,
        uint64  endTime,
        bytes32 reasonHash
    );

    event TaskCancelled(
        uint256 indexed id,
        uint64  endTime
    );

    mapping(uint256 => Task) public tasks;
    uint256 public nextId = 1;

    error TaskNotFound(uint256 id);
    error NotTaskAgent(uint256 id, address caller);
    error InvalidStatusTransition(uint256 id, Status from, Status to);
    error ParentNotFound(uint256 parentId);

    function startTask(
        uint256 parentId,
        bytes32 taskType,
        bytes32 descriptionHash,
        bytes32 eventSPO,
        string calldata cid
    ) external returns (uint256 id) {
        if (parentId != 0 && tasks[parentId].id == 0) revert ParentNotFound(parentId);

        id = nextId++;
        Task storage t = tasks[id];
        t.id              = id;
        t.agent           = msg.sender;
        t.parentId        = parentId;
        t.taskType        = taskType;
        t.descriptionHash = descriptionHash;
        t.eventSPO        = eventSPO;
        t.startTime       = uint64(block.timestamp);
        t.status          = Status.Started;
        t.cid             = cid;

        emit TaskStarted(id, msg.sender, parentId, taskType, descriptionHash, eventSPO, t.startTime, cid);
    }

    function completeTask(uint256 id, bytes32 resultHash, string calldata cid) external {
        Task storage t = _mutableTask(id);
        if (t.status != Status.Started) revert InvalidStatusTransition(id, t.status, Status.Completed);

        t.status     = Status.Completed;
        t.endTime    = uint64(block.timestamp);
        t.resultHash = resultHash;
        if (bytes(cid).length != 0) t.cid = cid;

        emit TaskCompleted(id, t.endTime, resultHash, t.cid);
    }

    function failTask(uint256 id, bytes32 reasonHash) external {
        Task storage t = _mutableTask(id);
        if (t.status != Status.Started) revert InvalidStatusTransition(id, t.status, Status.Failed);

        t.status     = Status.Failed;
        t.endTime    = uint64(block.timestamp);
        t.resultHash = reasonHash;

        emit TaskFailed(id, t.endTime, reasonHash);
    }

    function cancelTask(uint256 id) external {
        Task storage t = _mutableTask(id);
        if (t.status != Status.Started) revert InvalidStatusTransition(id, t.status, Status.Cancelled);

        t.status  = Status.Cancelled;
        t.endTime = uint64(block.timestamp);

        emit TaskCancelled(id, t.endTime);
    }

    function getTask(uint256 id) external view returns (Task memory) {
        if (tasks[id].id == 0) revert TaskNotFound(id);
        return tasks[id];
    }

    function _mutableTask(uint256 id) private view returns (Task storage t) {
        t = tasks[id];
        if (t.id == 0) revert TaskNotFound(id);
        if (t.agent != msg.sender) revert NotTaskAgent(id, msg.sender);
    }
}
