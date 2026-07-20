import SwiftUI

struct SettingsSheet: View {
    @AppStorage("baseURL") private var baseURL = "http://localhost:8000"
    @Environment(\.dismiss) private var dismiss
    @State private var status: Status = .idle

    enum Status {
        case idle, testing, ok, failed
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.1.10:8000", text: $baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } header: {
                    Text("POPFRAME server")
                } footer: {
                    Text("Base URL of the POPFRAME backend. Use your Mac's LAN IP (e.g. http://192.168.1.10:8000) when testing on a device; localhost only works in the simulator. Later this can point at Railway.")
                }

                Section {
                    Button {
                        testConnection()
                    } label: {
                        HStack {
                            Text("Test connection")
                            Spacer()
                            switch status {
                            case .idle:
                                EmptyView()
                            case .testing:
                                ProgressView()
                            case .ok:
                                Label("Connected", systemImage: "checkmark.circle.fill")
                                    .foregroundStyle(.green)
                                    .labelStyle(.iconOnly)
                            case .failed:
                                Label("Unreachable", systemImage: "xmark.circle.fill")
                                    .foregroundStyle(.red)
                                    .labelStyle(.iconOnly)
                            }
                        }
                    }
                    .disabled(status == .testing)
                } footer: {
                    if status == .failed {
                        Text("Could not reach \(UploadClient.normalizedBase(baseURL))/api/health. Check the URL and that the server is running.")
                    } else if status == .ok {
                        Text("Server is up.")
                    }
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium])
    }

    private func testConnection() {
        status = .testing
        Task {
            let ok = await UploadClient.health(base: baseURL)
            status = ok ? .ok : .failed
        }
    }
}

#Preview {
    SettingsSheet()
}
