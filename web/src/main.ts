// 应用入口：挂载 Pinia、Router、AntDV
import Antd from "ant-design-vue";
import "ant-design-vue/dist/reset.css";
import "./styles/global.css";
import dayjs from "dayjs";
import quarterOfYear from "dayjs/plugin/quarterOfYear"; // a-date-picker picker="quarter" 依赖
import { createPinia } from "pinia";
import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";

dayjs.extend(quarterOfYear);

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.use(Antd);
app.mount("#app");
