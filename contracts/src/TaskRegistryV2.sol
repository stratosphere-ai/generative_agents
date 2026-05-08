// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title TaskRegistryV2
/// @notice Append-only registry of tasks performed by autonomous agents.
///         Backwards-compatible with V1 (all V1 functions and events kept
///         with identical signatures), plus three batch/atomic helpers:
///
///           startTaskBatch(StartArgs[])        -> uint256[]
///           completeTaskBatch(CompleteArgs[])
///           recordAtomicAction(...)            -> uint256
///           recordAtomicActionBatch(StartArgs[], bytes32[])
///
///         Designed for sims that emit dozens of tasks per simulated day:
///         per-tx base gas (21000) is paid once instead of once per task,
///         and atomic actions (immediate start+complete) skip a full extra
///         tx. Storage layout is identical to V1 for an eventual proxy
///         migration; this contract is shipped as a fresh deploy in v2.
contract TaskRegistryV2 {
    enum Status { Started, Completed, Failed, Cancelled }

    struct Task {
        uint256 id;
        address agent;
        uint256 parentId;
        bytes32 taskType;
        bytes32 descriptionHash;
        bytes32 eventSPO;
        uint64  startTime;
        uint64  endTime;
        Status  status;
        bytes32 resultHash;
        string  cid;
    }

    struct StartArgs {
        uint256 parentId;
        bytes32 taskType;
        bytes32 descriptionHash;
        bytes32 eventSPO;
        string  cid;
    }

    struct CompleteArgs {
        uint256 id;
        bytes32 resultHash;
        string  cid;
    }

    // ---- Events: V1 set kept verbatim --------------------------------------

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

    // ---- New in V2: atomic-action shortcut event ---------------------------

    /// @dev Emitted when an action is started AND completed in one call.
    ///      Equivalent to TaskStarted + TaskCompleted on the same id, but
    ///      saves a Started→Completed status transition write and one tx.
    event AtomicActionRecorded(
        uint256 indexed id,
        address indexed agent,
        uint256 indexed parentId,
        bytes32 taskType,
        bytes32 descriptionHash,
        bytes32 eventSPO,
        bytes32 resultHash,
        uint64  timestamp,
        string  cid
    );

    // ---- Storage: layout matches V1 ----------------------------------------

    mapping(uint256 => Task) public tasks;
    uint256 public nextId = 1;

    // ---- Errors ------------------------------------------------------------

    error TaskNotFound(uint256 id);
    error NotTaskAgent(uint256 id, address caller);
    error InvalidStatusTransition(uint256 id, Status from, Status to);
    error ParentNotFound(uint256 parentId);
    error EmptyBatch();

    // =========================================================================
    // V1 surface — preserved verbatim
    // =========================================================================

    function startTask(
        uint256 parentId,
        bytes32 taskType,
        bytes32 descriptionHash,
        bytes32 eventSPO,
        string calldata cid
    ) external returns (uint256 id) {
        return _startTask(parentId, taskType, descriptionHash, eventSPO, cid);
    }

    function completeTask(uint256 id, bytes32 resultHash, string calldata cid) external {
        _completeTask(id, resultHash, cid);
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

    // =========================================================================
    // V2 batch surface
    // =========================================================================

    /// @notice Start N tasks in a single tx. Returns the assigned ids in input order.
    /// @dev Reverts atomically if any single start would revert (e.g. unknown parentId).
    function startTaskBatch(StartArgs[] calldata items)
        external
        returns (uint256[] memory ids)
    {
        uint256 n = items.length;
        if (n == 0) revert EmptyBatch();
        ids = new uint256[](n);
        for (uint256 i = 0; i < n; ++i) {
            StartArgs calldata a = items[i];
            ids[i] = _startTask(a.parentId, a.taskType, a.descriptionHash, a.eventSPO, a.cid);
        }
    }

    /// @notice Complete N tasks in a single tx.
    function completeTaskBatch(CompleteArgs[] calldata items) external {
        uint256 n = items.length;
        if (n == 0) revert EmptyBatch();
        for (uint256 i = 0; i < n; ++i) {
            CompleteArgs calldata a = items[i];
            _completeTask(a.id, a.resultHash, a.cid);
        }
    }

    /// @notice Start AND complete a task in one tx. Use for sub-actions whose
    ///         start and end are simultaneous in the sim (e.g. a sale).
    function recordAtomicAction(
        uint256 parentId,
        bytes32 taskType,
        bytes32 descriptionHash,
        bytes32 eventSPO,
        bytes32 resultHash,
        string calldata cid
    ) external returns (uint256 id) {
        return _recordAtomic(parentId, taskType, descriptionHash, eventSPO, resultHash, cid);
    }

    /// @notice Atomic-record a batch of actions. resultHashes is parallel to starts.
    function recordAtomicActionBatch(
        StartArgs[] calldata starts,
        bytes32[]   calldata resultHashes
    ) external returns (uint256[] memory ids) {
        uint256 n = starts.length;
        if (n == 0) revert EmptyBatch();
        require(resultHashes.length == n, "resultHashes length mismatch");
        ids = new uint256[](n);
        for (uint256 i = 0; i < n; ++i) {
            StartArgs calldata s = starts[i];
            ids[i] = _recordAtomic(s.parentId, s.taskType, s.descriptionHash, s.eventSPO, resultHashes[i], s.cid);
        }
    }

    // =========================================================================
    // Internals (shared between V1 surface and V2 batch surface)
    // =========================================================================

    function _startTask(
        uint256 parentId,
        bytes32 taskType,
        bytes32 descriptionHash,
        bytes32 eventSPO,
        string calldata cid
    ) private returns (uint256 id) {
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

    function _completeTask(uint256 id, bytes32 resultHash, string calldata cid) private {
        Task storage t = _mutableTask(id);
        if (t.status != Status.Started) revert InvalidStatusTransition(id, t.status, Status.Completed);
        t.status     = Status.Completed;
        t.endTime    = uint64(block.timestamp);
        t.resultHash = resultHash;
        if (bytes(cid).length != 0) t.cid = cid;
        emit TaskCompleted(id, t.endTime, resultHash, t.cid);
    }

    function _recordAtomic(
        uint256 parentId,
        bytes32 taskType,
        bytes32 descriptionHash,
        bytes32 eventSPO,
        bytes32 resultHash,
        string calldata cid
    ) private returns (uint256 id) {
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
        t.endTime         = uint64(block.timestamp);
        t.status          = Status.Completed;
        t.resultHash      = resultHash;
        t.cid             = cid;
        emit AtomicActionRecorded(
            id, msg.sender, parentId, taskType, descriptionHash, eventSPO, resultHash,
            t.startTime, cid
        );
    }

    function _mutableTask(uint256 id) private view returns (Task storage t) {
        t = tasks[id];
        if (t.id == 0) revert TaskNotFound(id);
        if (t.agent != msg.sender) revert NotTaskAgent(id, msg.sender);
    }
}
