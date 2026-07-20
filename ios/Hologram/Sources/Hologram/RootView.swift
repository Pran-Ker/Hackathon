import SwiftUI

enum Phase {
    case capture
    case scanning(CapturePayload)
    case hologram(scene: Scene3D, demo: Bool)
}

struct RootView: View {
    @State private var phase: Phase = .capture
    @State private var showSettings = false
    @AppStorage("baseURL") private var baseURL = "http://localhost:8000"

    var body: some View {
        NavigationStack {
            Group {
                switch phase {
                case .capture:
                    CaptureView(
                        onScan: { payload in phase = .scanning(payload) },
                        onDemo: { loadDemo() }
                    )
                case .scanning(let payload):
                    ScanningView(
                        payload: payload,
                        baseURL: baseURL,
                        onDone: { scene in phase = .hologram(scene: scene, demo: false) },
                        onDemo: { loadDemo() },
                        onCancel: { phase = .capture }
                    )
                case .hologram(let scene, let demo):
                    HologramView(
                        scene: scene,
                        baseURL: baseURL,
                        demoMode: demo,
                        onClose: { phase = .capture }
                    )
                }
            }
            .toolbar {
                if case .capture = phase {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button { showSettings = true } label: {
                            Image(systemName: "gearshape")
                        }
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsSheet()
            }
        }
    }

    private func loadDemo() {
        if let scene = UploadClient.demoScene() {
            phase = .hologram(scene: scene, demo: true)
        }
    }
}

#Preview {
    RootView()
}
