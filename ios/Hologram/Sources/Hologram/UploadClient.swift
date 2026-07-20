import Foundation

enum UploadError: LocalizedError {
    case badBaseURL
    case server(status: Int, body: String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .badBaseURL:
            return "The server URL looks invalid. Check it in Settings."
        case .server(let status, let body):
            let snippet = body.prefix(200)
            return "Server error \(status)\(snippet.isEmpty ? "" : ": \(snippet)")"
        case .decoding(let detail):
            return "Could not read the server response (\(detail))."
        }
    }
}

enum UploadClient {

    /// Long timeouts: an uncached scan takes ~60s (sometimes minutes).
    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 900
        config.timeoutIntervalForResource = 900
        config.waitsForConnectivity = true
        return URLSession(configuration: config)
    }()

    static func normalizedBase(_ raw: String) -> String {
        var base = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while base.hasSuffix("/") { base.removeLast() }
        if !base.contains("://") { base = "http://" + base }
        return base
    }

    static func viewerURL(base: String, sceneID: String) -> URL? {
        var comps = URLComponents(string: normalizedBase(base) + "/app/hologram.html")
        comps?.queryItems = [URLQueryItem(name: "scene", value: sceneID)]
        return comps?.url
    }

    /// POST multipart {file, prompt} to <base>/api/scene.
    static func scan(base: String, payload: CapturePayload) async throws -> Scene3D {
        guard let url = URL(string: normalizedBase(base) + "/api/scene") else {
            throw UploadError.badBaseURL
        }

        let boundary = "hologram-\(UUID().uuidString)"
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"\(payload.filename)\"\r\n")
        append("Content-Type: \(payload.mimeType)\r\n\r\n")
        body.append(payload.data)
        append("\r\n--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"prompt\"\r\n\r\n")
        append(payload.prompt)
        append("\r\n--\(boundary)--\r\n")

        let (data, response) = try await session.upload(for: request, from: body)

        guard let http = response as? HTTPURLResponse else {
            throw UploadError.decoding("no HTTP response")
        }
        guard (200...299).contains(http.statusCode) else {
            throw UploadError.server(status: http.statusCode,
                                     body: String(data: data, encoding: .utf8) ?? "")
        }
        do {
            return try JSONDecoder().decode(Scene3D.self, from: data)
        } catch {
            throw UploadError.decoding(error.localizedDescription)
        }
    }

    /// GET <base>/api/health — used by the "Test connection" button in Settings.
    static func health(base: String) async -> Bool {
        guard let url = URL(string: normalizedBase(base) + "/api/health") else { return false }
        var request = URLRequest(url: url)
        request.timeoutInterval = 5
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        return (200...299).contains(http.statusCode)
    }

    /// Bundled demo Scene3D so the app is demoable with the backend down.
    static func demoScene() -> Scene3D? {
        guard let url = Bundle.main.url(forResource: "SampleScene", withExtension: "json"),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(Scene3D.self, from: data)
    }
}
