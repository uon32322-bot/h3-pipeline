// ClipForge 唯一源码改动：src/lib/providers/atlas-cloud.ts 构造函数
// 目的：让 atlas-cloud provider 默认指向本地 H3 Bridge（单用户私有部署）


  constructor(config: ProviderConfig) {
    const resolvedBaseUrl =
      config.baseUrl || process.env.H3_BRIDGE_URL || 'http://127.0.0.1:8900/api/v1'
    super({
      ...config,
      baseUrl: resolvedBaseUrl,
    })
    // H3 Bridge routing diagnostic (single-user private deployment)
    console.log(
      '[H3 Bridge] AtlasCloudProvider baseUrl =',
      (this as unknown as { config: { baseUrl?: string } }).config?.baseUrl,
      '| incoming config.baseUrl =',
      JSON.stringify(config.baseUrl),
      '| env H3_BRIDGE_URL =',
      process.env.H3_BRIDGE_URL
    )
  }

  /**
   * Generate an image
   */
  async generateImage(options: ImageOptions): Promise<ImageResult> {
    const body = {
      model: options.modelId,
