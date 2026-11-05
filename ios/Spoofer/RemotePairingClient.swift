import Foundation
import Network

/// Client for RemotePairing (`remotepairingd`, TCP port 49152), the iOS 17+ service Xcode
/// uses for wireless developer connections. On iOS 26, lockdownd (62078) closes connections
/// that arrive through the loopback VPN, but RemotePairing is the modern path to the
/// developer tunnel anyway.
///
/// Wire format: "RPPairing" (9 bytes) + big-endian UInt16 body length + JSON body.
/// Messages are wrapped as {"message": {"plain": {"_0": ...}}, "originatedBy": "host",
/// "sequenceNumber": n}. Milestone 1 only performs the opening handshake, which needs no
/// pairing keys: the phone replies with its protocol version and options.
final class RemotePairingClient {
    static let port: NWEndpoint.Port = 49152
    static let magic = Data("RPPairing".utf8)
    static let wireProtocolVersion = 19

    enum RPError: LocalizedError {
        case closed, badMagic, badMessage(String)
        var errorDescription: String? {
            switch self {
            case .closed: return "Connection closed by the phone"
            case .badMagic: return "Reply wasn't a RemotePairing packet"
            case .badMessage(let m): return "Bad message: \(m)"
            }
        }
    }

    private let connection: NWConnection
    private let queue = DispatchQueue(label: "remotepairing")
    private var sequenceNumber = 0

    init(host: String) {
        let tcp = NWProtocolTCP.Options()
        tcp.connectionTimeout = 5
        tcp.enableKeepalive = true
        connection = NWConnection(host: NWEndpoint.Host(host), port: Self.port, using: NWParameters(tls: nil, tcp: tcp))
    }

    func connect() async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            let once = ResumeOnce()
            connection.stateUpdateHandler = { state in
                switch state {
                case .ready: if once.claim() { cont.resume() }
                case .failed(let e), .waiting(let e): if once.claim() { cont.resume(throwing: e) }
                case .cancelled: if once.claim() { cont.resume(throwing: RPError.closed) }
                default: break
                }
            }
            connection.start(queue: queue)
            queue.asyncAfter(deadline: .now() + 8) {
                if once.claim() { cont.resume(throwing: LockdownClient.LockdownError.timeout) }
            }
        }
    }

    func close() { connection.cancel() }

    /// Suspends until the phone closes the connection (or it fails). Returns a description.
    func waitForClose() async -> String {
        await withCheckedContinuation { cont in
            connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { data, _, isComplete, error in
                if let error { cont.resume(returning: error.localizedDescription) }
                else if isComplete { cont.resume(returning: "closed by the phone") }
                else { cont.resume(returning: "unexpected data (\(data?.count ?? 0) bytes)") }
            }
        }
    }

    /// Opening handshake. Returns the phone's handshake info (deviceOptions, wireProtocolVersion, ...).
    func handshake() async throws -> [String: Any] {
        let reply = try await sendPlain(["request": ["_0": ["handshake": ["_0": [
            "hostOptions": ["attemptPairVerify": true],
            "wireProtocolVersion": Self.wireProtocolVersion,
        ]]]]])
        guard let response = reply["response"] as? [String: Any],
              let one = response["_1"] as? [String: Any],
              let hs = one["handshake"] as? [String: Any],
              let info = hs["_0"] as? [String: Any] else {
            throw RPError.badMessage("unexpected handshake reply")
        }
        return info
    }

    private func sendPlain(_ plain: [String: Any]) async throws -> [String: Any] {
        let envelope: [String: Any] = [
            "message": ["plain": ["_0": plain]],
            "originatedBy": "host",
            "sequenceNumber": sequenceNumber,
        ]
        sequenceNumber += 1
        let body = try JSONSerialization.data(withJSONObject: envelope)
        var frame = Self.magic
        frame.append(contentsOf: withUnsafeBytes(of: UInt16(body.count).bigEndian) { Array($0) })
        frame.append(body)
        try await send(frame)

        let header = try await receive(exactly: Self.magic.count + 2)
        guard header.prefix(Self.magic.count) == Self.magic else { throw RPError.badMagic }
        let length = Int(header[header.startIndex + 9]) << 8 | Int(header[header.startIndex + 10])
        let reply = try await receive(exactly: length)
        guard let obj = try JSONSerialization.jsonObject(with: reply) as? [String: Any],
              let message = obj["message"] as? [String: Any],
              let plainReply = message["plain"] as? [String: Any],
              let inner = plainReply["_0"] as? [String: Any] else {
            throw RPError.badMessage("not a plain message")
        }
        return inner
    }

    private func send(_ data: Data) async throws {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            connection.send(content: data, completion: .contentProcessed { error in
                if let error { cont.resume(throwing: error) } else { cont.resume() }
            })
        }
    }

    private func receive(exactly count: Int) async throws -> Data {
        var buffer = Data()
        while buffer.count < count {
            let chunk: Data = try await withCheckedThrowingContinuation { cont in
                connection.receive(minimumIncompleteLength: 1, maximumLength: count - buffer.count) { data, _, isComplete, error in
                    if let error { cont.resume(throwing: error) }
                    else if let data, !data.isEmpty { cont.resume(returning: data) }
                    else if isComplete { cont.resume(throwing: RPError.closed) }
                    else { cont.resume(returning: Data()) }
                }
            }
            buffer.append(chunk)
        }
        return buffer
    }
}
