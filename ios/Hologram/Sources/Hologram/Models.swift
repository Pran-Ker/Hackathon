import Foundation

/// Scene3D response contract from POST /api/scene.
/// All fields decoded leniently so partial/evolving backend responses never crash the app.
struct Scene3D: Decodable {

    struct Transform: Decodable {
        let position: [Double]?
        let rotation: [Double]?
        let scale: ScaleValue?

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            position = try? c.decode([Double].self, forKey: .position)
            rotation = try? c.decode([Double].self, forKey: .rotation)
            scale = try? c.decode(ScaleValue.self, forKey: .scale)
        }

        enum CodingKeys: String, CodingKey { case position, rotation, scale }
    }

    /// Scale can arrive as a scalar or an [x,y,z] array.
    enum ScaleValue: Decodable {
        case uniform(Double)
        case xyz([Double])

        init(from decoder: Decoder) throws {
            let c = try decoder.singleValueContainer()
            if let d = try? c.decode(Double.self) { self = .uniform(d); return }
            self = .xyz(try c.decode([Double].self))
        }
    }

    struct ObjectInfo: Decodable {
        let name: String?
        let tags: [String]?
        let facts: [String]?

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            name = try? c.decode(String.self, forKey: .name)
            tags = try? c.decode([String].self, forKey: .tags)
            facts = try? c.decode([String].self, forKey: .facts)
        }

        enum CodingKeys: String, CodingKey { case name, tags, facts }
    }

    struct SceneObject: Decodable, Identifiable {
        let id: String
        let label: String?
        let glbURL: String?
        let splatURL: String?
        let cutoutURL: String?
        let bbox: [Double]?
        let score: Double?
        let transform: Transform?
        let info: ObjectInfo?

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            if let s = try? c.decode(String.self, forKey: .id) {
                id = s
            } else if let i = try? c.decode(Int.self, forKey: .id) {
                id = String(i)
            } else {
                id = UUID().uuidString
            }
            label = try? c.decode(String.self, forKey: .label)
            glbURL = try? c.decode(String.self, forKey: .glbURL)
            splatURL = try? c.decode(String.self, forKey: .splatURL)
            cutoutURL = try? c.decode(String.self, forKey: .cutoutURL)
            bbox = try? c.decode([Double].self, forKey: .bbox)
            score = try? c.decode(Double.self, forKey: .score)
            transform = try? c.decode(Transform.self, forKey: .transform)
            info = try? c.decode(ObjectInfo.self, forKey: .info)
        }

        enum CodingKeys: String, CodingKey {
            case id, label, bbox, score, transform, info
            case glbURL = "glb_url"
            case splatURL = "splat_url"
            case cutoutURL = "cutout_url"
        }

        var displayName: String { info?.name ?? label ?? "Object \(id)" }
    }

    let sceneID: String?
    let altID: String?
    let sceneGLBURL: String?
    let frameURL: String?
    let prompt: String?
    let objects: [SceneObject]
    let cached: Bool?
    let timings: [String: Double]?
    let error: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sceneID = try? c.decode(String.self, forKey: .sceneID)
        altID = try? c.decode(String.self, forKey: .altID)
        sceneGLBURL = try? c.decode(String.self, forKey: .sceneGLBURL)
        frameURL = try? c.decode(String.self, forKey: .frameURL)
        prompt = try? c.decode(String.self, forKey: .prompt)
        objects = (try? c.decode([SceneObject].self, forKey: .objects)) ?? []
        cached = try? c.decode(Bool.self, forKey: .cached)
        timings = try? c.decode([String: Double].self, forKey: .timings)
        error = try? c.decode(String.self, forKey: .error)
    }

    enum CodingKeys: String, CodingKey {
        case prompt, objects, cached, timings, error
        case sceneID = "scene_id"
        case altID = "id"
        case sceneGLBURL = "scene_glb_url"
        case frameURL = "frame_url"
    }

    /// The id used to build /app/hologram.html?scene=<id>.
    /// The documented contract has no explicit id field, so fall back to deriving
    /// one from the asset URLs: the directory segment containing the scene assets
    /// (e.g. /files/scene/<id>/scene.glb -> <id>). Flagged as an open item.
    var resolvedSceneID: String? {
        if let s = sceneID, !s.isEmpty { return s }
        if let s = altID, !s.isEmpty { return s }
        for candidate in [sceneGLBURL, frameURL] {
            guard let raw = candidate, !raw.isEmpty else { continue }
            let path = raw.split(separator: "?").first.map(String.init) ?? raw
            let parts = path.split(separator: "/").map(String.init)
            guard parts.count >= 2 else { continue }
            if let i = parts.lastIndex(where: { $0 == "scene" || $0 == "scenes" }),
               i + 1 < parts.count - 1 {
                return parts[i + 1]
            }
            return parts[parts.count - 2]
        }
        return nil
    }
}

/// What the Capture screen hands to the Scanning screen.
struct CapturePayload: Equatable {
    let data: Data
    let filename: String
    let mimeType: String
    let prompt: String
}
