import SwiftUI

struct ScanningView: View {
    let payload: CapturePayload
    let baseURL: String
    let onDone: (Scene3D) -> Void
    let onDemo: () -> Void
    let onCancel: () -> Void

    @State private var error: Error?
    @State private var elapsed = 0
    @State private var attempt = 0

    private let timer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            if let error {
                Image(systemName: "wifi.exclamationmark")
                    .font(.system(size: 52))
                    .foregroundStyle(.orange)
                Text("Couldn't reach the scanner")
                    .font(.title3.bold())
                Text(error.localizedDescription)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)

                VStack(spacing: 12) {
                    Button {
                        self.error = nil
                        elapsed = 0
                        attempt += 1
                    } label: {
                        Label("Retry", systemImage: "arrow.clockwise")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)

                    Button {
                        onDemo()
                    } label: {
                        Label("Show demo scene instead", systemImage: "sparkles")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                }
                .padding(.horizontal, 32)
            } else {
                ProgressView()
                    .controlSize(.large)
                Text("Scanning \"\(payload.prompt)\"…")
                    .font(.title3.bold())
                Text(statusLine)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
                Text("\(elapsed)s")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.tertiary)
            }

            Spacer()

            Button("Cancel", role: .cancel) { onCancel() }
                .padding(.bottom)
        }
        .navigationTitle("Scanning")
        .navigationBarTitleDisplayMode(.inline)
        .navigationBarBackButtonHidden(true)
        .onReceive(timer) { _ in
            if error == nil { elapsed += 1 }
        }
        .task(id: attempt) {
            do {
                let scene = try await UploadClient.scan(base: baseURL, payload: payload)
                if let apiError = scene.error, scene.objects.isEmpty {
                    throw UploadError.server(status: 200, body: apiError)
                }
                onDone(scene)
            } catch is CancellationError {
                // view dismissed
            } catch {
                self.error = error
            }
        }
    }

    private var statusLine: String {
        switch elapsed {
        case ..<3:
            return "Uploading… cached scenes come back instantly."
        case ..<15:
            return "Detecting \"\(payload.prompt)\" and cutting it out…"
        case ..<60:
            return "Reconstructing 3D — a fresh scan takes about a minute. Keep the app open."
        default:
            return "Still working — big scenes can take a few minutes. Keep the app open."
        }
    }
}
