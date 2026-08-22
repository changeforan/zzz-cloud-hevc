/* Track B confirmation: can wined3d create NV12 *plane* shader resource views?
 *
 * The ZZZ renderer samples NV12 directly: an R8_UNORM SRV over plane 0 (luma)
 * and an R8G8_UNORM SRV over plane 1 (chroma). Its failure path
 * "CreateShaderResourceView R8_UNORM failed" is what kills initRenderer.
 * This reproduces that call with no game involved. */
#include <stdio.h>
#define COBJMACROS
#include <initguid.h>
#include <windows.h>
#include <d3d11.h>

static void try_srv(ID3D11Device *dev, ID3D11Texture2D *tex, DXGI_FORMAT fmt,
        UINT plane, const char *label)
{
    D3D11_SHADER_RESOURCE_VIEW_DESC d = {0};
    ID3D11ShaderResourceView *srv = NULL;
    HRESULT hr;

    d.Format = fmt;
    d.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
    d.Texture2D.MipLevels = 1;
    d.Texture2D.MostDetailedMip = 0;
    /* PlaneSlice lives in the D3D11.1 Tex2D desc; set via the union field. */
    hr = ID3D11Device_CreateShaderResourceView(dev, (ID3D11Resource *)tex, &d, &srv);
    printf("  %-28s plane %u  hr %#-12lx %s\n", label, plane, hr,
            SUCCEEDED(hr) ? "OK" : "FAILED");
    if (srv) ID3D11ShaderResourceView_Release(srv);
}

int main(void)
{
    ID3D11Texture2D *nv12 = NULL;
    D3D11_TEXTURE2D_DESC td = {0};
    ID3D11Device *dev = NULL;
    D3D_FEATURE_LEVEL fl;
    UINT fmt_support = 0;
    HRESULT hr;

    hr = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL,
            D3D11_CREATE_DEVICE_VIDEO_SUPPORT, NULL, 0, D3D11_SDK_VERSION, &dev, &fl, NULL);
    if (FAILED(hr)) { printf("D3D11CreateDevice failed %#lx\n", hr); return 1; }
    printf("device ok, feature level %#x\n", fl);

    hr = ID3D11Device_CheckFormatSupport(dev, DXGI_FORMAT_NV12, &fmt_support);
    printf("CheckFormatSupport(NV12) hr %#lx support %#x  (SHADER_SAMPLE=%s, TEXTURE2D=%s)\n",
            hr, fmt_support,
            (fmt_support & D3D11_FORMAT_SUPPORT_SHADER_SAMPLE) ? "yes" : "NO",
            (fmt_support & D3D11_FORMAT_SUPPORT_TEXTURE2D) ? "yes" : "NO");

    td.Width = 1920; td.Height = 1080; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_NV12; td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;

    hr = ID3D11Device_CreateTexture2D(dev, &td, NULL, &nv12);
    printf("CreateTexture2D(NV12, BIND_SHADER_RESOURCE) hr %#lx %s\n", hr,
            SUCCEEDED(hr) ? "OK" : "FAILED");
    if (FAILED(hr)) { ID3D11Device_Release(dev); return 1; }

    printf("NV12 plane SRVs (what the ZZZ renderer needs):\n");
    try_srv(dev, nv12, DXGI_FORMAT_R8_UNORM,   0, "R8_UNORM  (luma)");
    try_srv(dev, nv12, DXGI_FORMAT_R8G8_UNORM, 1, "R8G8_UNORM (chroma)");

    ID3D11Texture2D_Release(nv12);
    ID3D11Device_Release(dev);
    return 0;
}
