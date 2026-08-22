/*
 * Step 5: minimal D3D11 video decode capability probe.
 *
 * Mirrors exactly what the ZZZ Cloud client does, so that Step 6 can be
 * verified without launching the game:
 *   D3D11CreateDevice -> ID3D11VideoDevice
 *   GetVideoDecoderProfileCount / GetVideoDecoderProfile   (enumeration)
 *   CheckVideoDecoderFormat                                (the call the client uses)
 *
 * Build (mingw):
 *   x86_64-w64-mingw32-gcc -o vidprobe.exe vidprobe.c -ld3d11 -ldxgi -lole32 -luuid
 */

#include <stdio.h>
#define COBJMACROS
#include <initguid.h>
#include <windows.h>
#include <d3d11.h>
#include <dxva.h>

static const struct { const GUID *guid; const char *name; } known[] =
{
    { &DXVA_ModeH264_VLD_NoFGT,   "H264_VLD_NoFGT"   },
    { &DXVA_ModeHEVC_VLD_Main,    "HEVC_VLD_Main"    },
    { &DXVA_ModeHEVC_VLD_Main10,  "HEVC_VLD_Main10"  },
    { &DXVA_ModeAV1_VLD_Profile0, "AV1_VLD_Profile0" },
};

static const char *guid_name(const GUID *g)
{
    for (unsigned i = 0; i < ARRAYSIZE(known); ++i)
        if (IsEqualGUID(known[i].guid, g))
            return known[i].name;
    return "(unrecognised)";
}

static void print_guid(const GUID *g)
{
    printf("{%08lx-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x}",
            g->Data1, g->Data2, g->Data3,
            g->Data4[0], g->Data4[1], g->Data4[2], g->Data4[3],
            g->Data4[4], g->Data4[5], g->Data4[6], g->Data4[7]);
}

int main(void)
{
    ID3D11VideoDevice *video = NULL;
    ID3D11Device *device = NULL;
    D3D_FEATURE_LEVEL level;
    UINT count;
    HRESULT hr;

    /* D3D11_CREATE_DEVICE_VIDEO_SUPPORT is what the client passes (flags 0x800). */
    hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL,
            D3D11_CREATE_DEVICE_VIDEO_SUPPORT, NULL, 0, D3D11_SDK_VERSION,
            &device, &level, NULL);
    if (FAILED(hr))
    {
        printf("D3D11CreateDevice failed, hr %#lx\n", hr);
        return 1;
    }
    printf("D3D11CreateDevice ok, feature level %#x\n", level);

    hr = ID3D11Device_QueryInterface(device, &IID_ID3D11VideoDevice, (void **)&video);
    if (FAILED(hr))
    {
        printf("QueryInterface(ID3D11VideoDevice) failed, hr %#lx\n", hr);
        ID3D11Device_Release(device);
        return 1;
    }
    printf("ID3D11VideoDevice ok\n\n");

    count = ID3D11VideoDevice_GetVideoDecoderProfileCount(video);
    printf("GetVideoDecoderProfileCount -> %u\n", count);

    for (UINT i = 0; i < count; ++i)
    {
        GUID profile;

        hr = ID3D11VideoDevice_GetVideoDecoderProfile(video, i, &profile);
        if (FAILED(hr))
        {
            printf("  [%u] GetVideoDecoderProfile failed, hr %#lx\n", i, hr);
            continue;
        }
        printf("  [%u] ", i);
        print_guid(&profile);
        printf("  %s\n", guid_name(&profile));
    }

    printf("\nCheckVideoDecoderFormat (DXGI_FORMAT_NV12):\n");
    for (unsigned i = 0; i < ARRAYSIZE(known); ++i)
    {
        BOOL supported = FALSE;

        hr = ID3D11VideoDevice_CheckVideoDecoderFormat(video, known[i].guid,
                DXGI_FORMAT_NV12, &supported);
        printf("  %-18s hr %#-10lx supported %s\n", known[i].name, hr,
                FAILED(hr) ? "?" : (supported ? "YES" : "no"));
    }

    /* Step 7: actually create decoders. GetVideoDecoderConfigCount is still a
     * stub upstream, so build the config by hand: ConfigBitstreamRaw 1 means
     * short slice control, which is the only form DXVA defines for HEVC. */
    printf("\nCreateVideoDecoder (1920x1080, NV12):\n");
    for (unsigned i = 0; i < ARRAYSIZE(known); ++i)
    {
        D3D11_VIDEO_DECODER_CONFIG config = {0};
        D3D11_VIDEO_DECODER_DESC desc = {0};
        ID3D11VideoDecoder *decoder = NULL;
        BOOL supported = FALSE;

        if (FAILED(ID3D11VideoDevice_CheckVideoDecoderFormat(video, known[i].guid,
                DXGI_FORMAT_NV12, &supported)) || !supported)
        {
            printf("  %-18s skipped (not supported)\n", known[i].name);
            continue;
        }

        desc.Guid = *known[i].guid;
        desc.SampleWidth = 1920;
        desc.SampleHeight = 1080;
        desc.OutputFormat = DXGI_FORMAT_NV12;
        config.ConfigBitstreamRaw = 1;

        hr = ID3D11VideoDevice_CreateVideoDecoder(video, &desc, &config, &decoder);
        printf("  %-18s hr %#-10lx %s\n", known[i].name, hr,
                SUCCEEDED(hr) ? "CREATED" : "failed");
        if (SUCCEEDED(hr))
            ID3D11VideoDecoder_Release(decoder);
    }

    ID3D11VideoDevice_Release(video);
    ID3D11Device_Release(device);
    return 0;
}
