// 核心类型定义：角色、用户、登录响应
export type Role = "employee" | "finance_staff" | "finance_manager" | "admin";

export interface UserOut {
  id: number;
  username: string;
  role: Role;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: UserOut;
}

export const ROLE_LABELS: Record<Role, string> = {
  employee: "普通员工",
  finance_staff: "财务专员",
  finance_manager: "财务主管",
  admin: "系统管理员",
};
