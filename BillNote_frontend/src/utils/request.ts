import axios, { AxiosInstance, AxiosResponse } from 'axios';
import toast from 'react-hot-toast'

// 统一响应类型
export interface IResponse<T = any> {
  code: number;
  msg: string;
  data: T;
}

// 模拟一个消息提示函数 (实际项目中会使用UI库的组件，如 Ant Design 的 message 或 Element UI 的 ElMessage)
// This function simulates a message display (in real projects, you'd use a UI library's component)

const baseURL = import.meta.env.VITE_API_BASE_URL;

const request: AxiosInstance = axios.create({
  baseURL: baseURL || '/api',
  timeout: 10000,
});

type ErrorPayload = {
  msg?: string;
  detail?: string;
  data?: any;
};

const resolveErrorMessage = (payload?: ErrorPayload): string | undefined => {
  if (!payload) {
    return undefined;
  }
  if (typeof payload.detail === 'string' && payload.detail.trim()) {
    return payload.detail.trim();
  }
  if (payload.data && typeof payload.data === 'object') {
    const detailFromData = payload.data.detail;
    if (typeof detailFromData === 'string' && detailFromData.trim()) {
      return detailFromData.trim();
    }
  }
  if (typeof payload.msg === 'string' && payload.msg.trim()) {
    return payload.msg.trim();
  }
  return undefined;
};

const showError = (message?: string, fallback = '操作失败，请稍后再试') => {
  toast.error(message || fallback);
};

request.interceptors.response.use(
  (response: AxiosResponse<IResponse>) => {
    const res = response.data;
    if (res.code === 0) {
      return res.data;
    }
    const errorMessage = resolveErrorMessage({
      msg: res.msg,
      data: res.data,
    });
    showError(errorMessage);
    return Promise.reject(res);
  },
  (error) => {
    const res = error?.response?.data as (IResponse & { detail?: string }) | undefined;
    if (res) {
      const errorMessage = resolveErrorMessage(res);
      showError(errorMessage, '服务器错误，请稍后再试');
      return Promise.reject(res);
    }
    showError('请求失败，请检查网络连接或稍后再试');
    return Promise.reject({
      code: -1,
      msg: '请求失败，请检查网络连接',
      data: null,
    } as IResponse);
  }
);

export default request
