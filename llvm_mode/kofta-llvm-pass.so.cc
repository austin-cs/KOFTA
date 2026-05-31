#define KOFTA_OPT_ANALYSIS_PASS

#include "llvm/IR/BasicBlock.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/GlobalVariable.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/IRBuilder.h"
#include "llvm/IR/LegacyPassManager.h"
#include "llvm/IR/Module.h"
#include "llvm/Pass.h"
#include "llvm/Support/raw_ostream.h"
#include "llvm/Transforms/IPO/PassManagerBuilder.h"

#include "../config.h"
#include "../debug.h"

#include <unistd.h>

#include <cctype>
#include <cstdlib>
#include <fstream>
#include <map>
#include <string>
#include <utility>
#include <vector>

using namespace llvm;

namespace {

  class OptionsMap {

  public:
    OptionsMap(unsigned int map_id) : map_id(map_id) { }
    ~OptionsMap() = default;

    void dump(std::ofstream &ofs) const {
      if (!ofs || size() == 0) {
        return;
      }
      ofs << map_id << ' ' << size() << "\n";
      for (const auto &option : options) {
        ofs << option.second << ' ' << option.first << "\n";
      }
    }

    void addOption(const std::string &name, int hasArg) {
      if (options.count(name)) return;
      options[name] = hasArg;
    }

    size_t size() const {
      return options.size();
    }

  private:
    unsigned int map_id;

    // < option name, has_arg >
    std::map<std::string, int> options;
  };

  class KOFTAAnalysis : public ModulePass {

  public:

    static char ID;
    KOFTAAnalysis() : ModulePass(ID) { }

    bool runOnModule(Module &M) override;

    StringRef getPassName() const override { return "KOFTAPass"; }

    void getAnalysisUsage(AnalysisUsage &AU) const override {
      AU.addRequired<DominatorTreeWrapperPass>();
    }

  private:

    Module *CurModule;

    Type *VoidTy;

    IntegerType *Int8Ty;
    IntegerType *Int16Ty;
    IntegerType *Int32Ty;
    IntegerType *Int64Ty;

    PointerType *Int8PtrTy;
    PointerType *Int64PtrTy;

    ConstantInt *CnstNegOne;

    ConstantInt *ModuleID;

    FunctionCallee FuncOptAnalysis;
    FunctionCallee optAnalysisProto(Module &M);

    FunctionCallee SanCovFuncTraceConstCmp[4];
    FunctionCallee sanCovTraceConstCmpProto(Module &M, u8 bytes);

    FunctionCallee SanCovFuncTraceSwitch;
    FunctionCallee sanCovTraceSwitchProto(Module &M);

    FunctionCallee FuncTraceStr;
    FunctionCallee traceStrProto(Module &M);

    void initVars(Module &M);

    size_t extractOptions(Instruction *Inst, std::ofstream &kofta_opts);

    void parseOpts(CallInst *CI, OptionsMap &options);
    void parseStrcmp(Value *OptString, OptionsMap &options);

    void sanitizerCovTraceConstCmp(ICmpInst *ICMP, const DominatorTree *DT);
    void sanitizerCovTraceSwitch(SwitchInst *SI);
    void sanitizerCovTraceString(CallInst *CI, Value *Str1, Value *Str2, Value *Len = nullptr);

    // ---- SHS source-context map (gated on $KOFTA_SRCMAP) -------------------
    // When enabled, each instrumented comparison whose operand the runtime
    // hint pool can observe (a const int for trace_const_cmp/switch, a string
    // literal for trace_str) gets a record written to the srcmap file: the
    // observed operand (the correlation key afl-fuzz uses to find a stuck
    // branch's source), its file:line, and a +/-k source-line slice captured
    // here at compile time so it travels with the build, not the fuzzing host.
    std::ofstream KoftaSrcmap;
    unsigned SrcCtx = 3;
    std::map<std::string, std::vector<std::string>> SrcCache;

    void emitSrcmap(Instruction *I, const char *kind, const std::string &operand);
    const std::vector<std::string> &sourceLines(const std::string &path);
    static std::string constStr(Value *V);
  };

} // end anonymous namespace

char KOFTAAnalysis::ID = 0;

bool KOFTAAnalysis::runOnModule(Module &M) {

  if (isatty(2) && !getenv("AFL_QUIET")) {
    SAYF(cCYA "kofta-llvm-pass " cBRI KOFTA_VERSION cRST " by <me@alardutp.dev>\n");
  }

  initVars(M);

  char *kofta_opt_save = getenv("KOFTA_OPTSAVE");
  if (!kofta_opt_save) {
    FATAL("Please set KOFTA_OPTSAVE.");
  }
  size_t opt_count = 0;
  std::ofstream kofta_opts;
  kofta_opts.open(kofta_opt_save, std::ios_base::app);

  if (char *srcmap = getenv("KOFTA_SRCMAP")) {
    KoftaSrcmap.open(srcmap, std::ios_base::app);
    if (char *ctx = getenv("KOFTA_SRCMAP_CTX")) {
      int k = atoi(ctx);
      if (k >= 0) SrcCtx = (unsigned)k;
    }
  }

  for (Function &F : M) {
    if (F.empty()) continue;
    if (F.getName().find(".module_ctor") != std::string::npos)
      continue;
    if (F.getName().startswith("__sanitizer_") ||
        F.getName().startswith("__kofta_") ||
        F.getName().startswith("__afl_"))
      continue;
    if (F.getLinkage() == GlobalValue::AvailableExternallyLinkage)
      continue;
    if (F.getName() == "__local_stdio_printf_options" ||
        F.getName() == "__local_stdio_scanf_options")
      continue;
    if (isa<UnreachableInst>(F.getEntryBlock().getTerminator()))
      continue;
    DominatorTree &DT = getAnalysis<DominatorTreeWrapperPass>(F).getDomTree();
    for (BasicBlock &BB : F) {
      for (Instruction &I : BB) {
        if      (isa<CallInst>(&I))   opt_count += extractOptions(&I, kofta_opts);
        else if (isa<ICmpInst>(&I))   sanitizerCovTraceConstCmp(cast<ICmpInst>(&I), &DT);
        else if (isa<SwitchInst>(&I)) sanitizerCovTraceSwitch(cast<SwitchInst>(&I));
      }
    }
  }

  kofta_opts.close();
  if (KoftaSrcmap.is_open()) KoftaSrcmap.close();
  if (opt_count) {
    OKF("Found %zu options. See %s.", opt_count, kofta_opt_save);
  }

  // This pass modifies the program.
  return true;

}

FunctionCallee KOFTAAnalysis::optAnalysisProto(Module &M) {

  FunctionType *FT =
      FunctionType::get(VoidTy, { Int16Ty }, false);
  FunctionCallee FC = M.getOrInsertFunction("__kofta_opt_analysis", FT);

  return FC;

}

FunctionCallee KOFTAAnalysis::sanCovTraceConstCmpProto(Module &M, u8 bytes) {

  IntegerType *IntTy = bytes == 1 ? Int8Ty :
                       bytes == 2 ? Int16Ty :
                       bytes == 4 ? Int32Ty : Int64Ty;
  FunctionType *FT =
      FunctionType::get(VoidTy, { IntTy, IntTy }, false);
  FunctionCallee FC = M.getOrInsertFunction("__sanitizer_cov_trace_const_cmp" + std::to_string(bytes), FT);

  return FC;

}

FunctionCallee KOFTAAnalysis::sanCovTraceSwitchProto(Module &M) {

  FunctionType *FT =
      FunctionType::get(VoidTy, { Int64Ty, Int64PtrTy }, false);
  FunctionCallee FC = M.getOrInsertFunction("__sanitizer_cov_trace_switch", FT);

  return FC;

}

FunctionCallee KOFTAAnalysis::traceStrProto(Module &M) {

  FunctionType *FT =
      FunctionType::get(VoidTy, { Int8PtrTy, Int8PtrTy, Int64Ty }, false);
  FunctionCallee FC = M.getOrInsertFunction("__kofta_trace_str", FT);

  return FC;

}

void KOFTAAnalysis::initVars(Module &M) {

  LLVMContext &C = M.getContext();

  CurModule = &M;

  VoidTy = Type::getVoidTy(C);

  Int8Ty = Type::getInt8Ty(C);
  Int16Ty = Type::getInt16Ty(C);
  Int32Ty = Type::getInt32Ty(C);
  Int64Ty = Type::getInt64Ty(C);

  Int8PtrTy = Type::getInt8PtrTy(C);
  Int64PtrTy = Type::getInt64PtrTy(C);

  CnstNegOne = ConstantInt::get(Int64Ty, -1);

  ModuleID = ConstantInt::get(Int16Ty, R(MAP_SIZE));

  FuncOptAnalysis = optAnalysisProto(M);

  SanCovFuncTraceConstCmp[0] = sanCovTraceConstCmpProto(M, 1);
  SanCovFuncTraceConstCmp[1] = sanCovTraceConstCmpProto(M, 2);
  SanCovFuncTraceConstCmp[2] = sanCovTraceConstCmpProto(M, 4);
  SanCovFuncTraceConstCmp[3] = sanCovTraceConstCmpProto(M, 8);

  SanCovFuncTraceSwitch = sanCovTraceSwitchProto(M);

  FuncTraceStr = traceStrProto(M);

}

size_t KOFTAAnalysis::extractOptions(Instruction *Inst, std::ofstream &kofta_opts) {

  CallInst *CI = dyn_cast<CallInst>(Inst);
  Function *CalledFunc = CI->getCalledFunction();
  if (!CalledFunc) return 0;

  unsigned int called_func_id = R(MAP_SIZE);
  OptionsMap options(called_func_id);

  // // Check if this call is to '*getopt*'
  if (CalledFunc->getName().contains("getopt")) {
    parseOpts(CI, options);
  }
  // Check if this call is to 'strcmp'
  else if (CalledFunc->getName().endswith("strcmp") || CalledFunc->getName().endswith("strcasecmp")) {
    parseStrcmp(CI->getArgOperand(0), options);
    parseStrcmp(CI->getArgOperand(1), options);
    sanitizerCovTraceString(CI, CI->getArgOperand(0), CI->getArgOperand(1));
  }
  else if (CalledFunc->getName().endswith("strncmp") || CalledFunc->getName().endswith("strncasecmp") || CalledFunc->getName().endswith("memcmp")) {
    parseStrcmp(CI->getArgOperand(0), options);
    parseStrcmp(CI->getArgOperand(1), options);
    sanitizerCovTraceString(CI, CI->getArgOperand(0), CI->getArgOperand(1), CI->getArgOperand(2));
  }

  if (options.size()) {
    IRBuilder<> IRB(Inst);
    ConstantInt *CalledFuncID = ConstantInt::get(Int16Ty, called_func_id);
    IRB.CreateCall(FuncOptAnalysis, { CalledFuncID });
  }

  options.dump(kofta_opts);
  return options.size();

}

void KOFTAAnalysis::parseOpts(CallInst *CI, OptionsMap &options) {

  for (unsigned i = 0, e = CI->getNumArgOperands(); i < e; ++i) {

    Value *Arg = CI->getArgOperand(i);

    if (LoadInst *LI = dyn_cast<LoadInst>(Arg)) {
      if (!LI->getPointerOperandType()->isPointerTy()) continue;
      GlobalVariable *GV = dyn_cast<GlobalVariable>(LI->getPointerOperand());
      if (!GV || !GV->hasInitializer()) continue;
      Arg = GV->getInitializer();
    }

    ConstantExpr *CE = dyn_cast<ConstantExpr>(Arg);
    if (!CE || CE->getOpcode() != Instruction::GetElementPtr) continue;
    GlobalVariable *GV = dyn_cast<GlobalVariable>(CE->getOperand(0));
    if (!GV || !GV->hasInitializer()) continue;

    Arg = GV->getInitializer();

    if (ConstantDataArray *CDA = dyn_cast<ConstantDataArray>(Arg)) {

      StringRef OptStr = CDA->getAsCString();
      if (OptStr.empty()) continue;

      for (unsigned j = 0, n = OptStr.size(); j < n; ++j) {
        char optChar = OptStr[j];
        if (optChar == ':')
          continue;
        bool requiredArg = (j + 1) < n && OptStr[j + 1] == ':';
        bool optionalArg = requiredArg && (j + 2) < n && OptStr[j + 2] == ':';
        options.addOption("-" + std::string{optChar}, optionalArg ? 2 : (requiredArg ? 1 : 0));
      }

    }
    else if (ConstantArray *CA = dyn_cast<ConstantArray>(Arg)) {

      for (unsigned j = 0, n = CA->getNumOperands(); j < n; ++j) {

        Constant *Elem = CA->getOperand(j);
        ConstantStruct *OptLong = dyn_cast<ConstantStruct>(Elem);
        if (!OptLong) continue;

        GlobalVariable *GV = dyn_cast<GlobalVariable>(OptLong->getOperand(0)->stripPointerCasts());
        if (!GV || !GV->hasInitializer()) continue;
        ConstantDataArray *NameField = dyn_cast<ConstantDataArray>(GV->getInitializer());
        if (!NameField || !NameField->isCString()) continue;

        std::string option_long = NameField->getAsCString().str();
        std::string option_short;
        int has_arg_value = 2;

        size_t pos = option_long.find_first_not_of('-');
        if (pos) option_long.erase(0, pos);
        pos = option_long.find_first_of('=');
        if (pos != std::string::npos) {
          option_long.erase(pos);
          has_arg_value = 1;
        }
        option_long.insert(0, "--");

        if (OptLong->getNumOperands() == 4) {
          Constant *ShortField = OptLong->getOperand(3);
          if (auto *ShortInt = dyn_cast<ConstantInt>(ShortField)) {
            char short_value = ShortInt->getSExtValue();
            if (isalpha(short_value)) {
              option_short.push_back('-');
              option_short.push_back(short_value);
            }
          }
          Constant *HasArgField = OptLong->getOperand(1);
          if (auto *HasArgInt = dyn_cast<ConstantInt>(HasArgField)) {
            has_arg_value = HasArgInt->getSExtValue();
          }
        }

        if (!option_short.empty()) {
          options.addOption(option_short, has_arg_value);
        } else {
          options.addOption(option_long, has_arg_value);
        }

      }

    }

  }

}

void KOFTAAnalysis::parseStrcmp(Value *OptString, OptionsMap &options) {
  std::string opt_str;

  if (auto *CE = dyn_cast<ConstantExpr>(OptString)) {
    if (CE->getOpcode() == Instruction::GetElementPtr) {
      if (GlobalVariable *GV = dyn_cast<GlobalVariable>(CE->getOperand(0))) {
        if (GV->hasInitializer())
          if (ConstantDataArray *CDA = dyn_cast<ConstantDataArray>(GV->getInitializer())) {
            if (CDA->isCString()) {
              StringRef CString = CDA->getAsCString();
              if (CString.size() > 1 && CString.startswith("-")) {
                opt_str = CString.str();
              }
            }
          }
      }
    }
  }

  if (!opt_str.empty()) {
    options.addOption(opt_str, 2);
  }
}

static bool IsBackEdge(BasicBlock *From, BasicBlock *To,
                       const DominatorTree *DT) {
  if (DT->dominates(To, From))
    return true;
  if (auto Next = To->getUniqueSuccessor())
    if (DT->dominates(Next, From))
      return true;
  return false;
}

void KOFTAAnalysis::sanitizerCovTraceConstCmp(ICmpInst *ICMP, const DominatorTree *DT) {

  if (ICMP->hasOneUse())
    if (auto BR = dyn_cast<BranchInst>(ICMP->user_back()))
      for (BasicBlock *B : BR->successors())
        if (IsBackEdge(BR->getParent(), B, DT))
          return;

  IRBuilder<> IRB(ICMP);
  Value *A0 = ICMP->getOperand(0);
  Value *A1 = ICMP->getOperand(1);
  if (!A0->getType()->isIntegerTy()) return;
  uint64_t TypeSize = A0->getType()->getIntegerBitWidth();
  int CallbackIdx = TypeSize == 8 ? 0 :
                    TypeSize == 16 ? 1 :
                    TypeSize == 32 ? 2 :
                    TypeSize == 64 ? 3 : -1;
  if (CallbackIdx < 0) return;
  bool FirstIsConst = isa<ConstantInt>(A0);
  bool SecondIsConst = isa<ConstantInt>(A1);
  if (FirstIsConst == SecondIsConst) return;
  // If only one is const, then make it the first callback argument.
  auto CallbackFunc = SanCovFuncTraceConstCmp[CallbackIdx];
  if (SecondIsConst) std::swap(A0, A1);

  IRB.CreateCall(CallbackFunc, {A0, A1});

  if (KoftaSrcmap.is_open())
    if (auto *C = dyn_cast<ConstantInt>(A0))
      emitSrcmap(ICMP, "cmp", std::to_string(C->getZExtValue()));

}

void KOFTAAnalysis::sanitizerCovTraceSwitch(SwitchInst *SI) {

  IRBuilder<> IRB(SI);
  SmallVector<Constant *, 16> Initializers;
  Value *Cond = SI->getCondition();
  if (Cond->getType()->getScalarSizeInBits() > Int64Ty->getScalarSizeInBits())
    return;
  Initializers.push_back(ConstantInt::get(Int64Ty, SI->getNumCases()));
  Initializers.push_back(ConstantInt::get(Int64Ty, Cond->getType()->getScalarSizeInBits()));
  if (Cond->getType()->getScalarSizeInBits() < Int64Ty->getScalarSizeInBits())
    Cond = IRB.CreateIntCast(Cond, Int64Ty, false);
  for (auto It : SI->cases()) {
    Constant *C = It.getCaseValue();
    if (C->getType()->getScalarSizeInBits() < Int64Ty->getScalarSizeInBits())
      C = ConstantExpr::getCast(CastInst::ZExt, It.getCaseValue(), Int64Ty);
    Initializers.push_back(C);
  }
  llvm::sort(Initializers.begin() + 2, Initializers.end(),
             [](const Constant *A, const Constant *B) {
               return cast<ConstantInt>(A)->getLimitedValue() <
                      cast<ConstantInt>(B)->getLimitedValue();
             });
  ArrayType *ArrayOfInt64Ty = ArrayType::get(Int64Ty, Initializers.size());
  GlobalVariable *GV = new GlobalVariable(
      *CurModule, ArrayOfInt64Ty, false, GlobalVariable::InternalLinkage,
      ConstantArray::get(ArrayOfInt64Ty, Initializers),
      "__sancov_gen_cov_switch_values");
  IRB.CreateCall(SanCovFuncTraceSwitch, {Cond, IRB.CreatePointerCast(GV, Int64PtrTy)});

  if (KoftaSrcmap.is_open())
    for (auto It : SI->cases())
      emitSrcmap(SI, "switch",
                 std::to_string(It.getCaseValue()->getZExtValue()));

}

void KOFTAAnalysis::sanitizerCovTraceString(CallInst *CI, Value *Str1, Value *Str2, Value *Len) {

  Value *Argv = Str1;
  Value *Cnst = Str2;

  if (Len == nullptr) Len = CnstNegOne;

  if (dyn_cast<ConstantExpr>(Str1)) {
    if (dyn_cast<ConstantExpr>(Str2)) return; // Both arguments are constant expressions.
    Cnst = Str1;
    Argv = Str2;
  }

  if (ConstantExpr *CE = dyn_cast<ConstantExpr>(Cnst)) {
    if (CE->getOpcode() == Instruction::GetElementPtr) {
      if (GlobalVariable *GV = dyn_cast<GlobalVariable>(CE->getOperand(0))) {
        if (GV->hasInitializer())
          if (ConstantDataArray *CDA = dyn_cast<ConstantDataArray>(GV->getInitializer())) {
            if (!CDA->isCString()) return; // Not a C-style string.
            StringRef CString = CDA->getAsCString();
            if (CString.startswith("-")) return; // Skip if the string starts with a dash.
          }
      }
    }
  }

  IRBuilder<> IRB(CI);
  IRB.CreateCall(FuncTraceStr, { Cnst, Argv, Len });

  if (KoftaSrcmap.is_open()) {
    std::string s = constStr(Cnst);
    if (!s.empty()) emitSrcmap(CI, "str", s);
  }

}

std::string KOFTAAnalysis::constStr(Value *V) {
  auto *CE = dyn_cast<ConstantExpr>(V);
  if (!CE || CE->getOpcode() != Instruction::GetElementPtr) return "";
  auto *GV = dyn_cast<GlobalVariable>(CE->getOperand(0));
  if (!GV || !GV->hasInitializer()) return "";
  auto *CDA = dyn_cast<ConstantDataArray>(GV->getInitializer());
  if (!CDA || !CDA->isCString()) return "";
  return CDA->getAsCString().str();
}

const std::vector<std::string> &KOFTAAnalysis::sourceLines(const std::string &path) {
  auto it = SrcCache.find(path);
  if (it != SrcCache.end()) return it->second;
  std::vector<std::string> &lines = SrcCache[path];
  std::ifstream ifs(path);
  for (std::string line; std::getline(ifs, line); )
    lines.push_back(line);
  return lines;
}

void KOFTAAnalysis::emitSrcmap(Instruction *I, const char *kind,
                               const std::string &operand) {
  const DebugLoc &DL = I->getDebugLoc();
  if (!DL) return;
  DILocation *Loc = DL.get();
  if (!Loc) return;
  unsigned line = Loc->getLine();
  if (!line) return;

  StringRef file = Loc->getFilename();
  StringRef dir = Loc->getDirectory();
  std::string path = file.str();
  if (!file.startswith("/") && !dir.empty())
    path = dir.str() + "/" + file.str();

  const std::vector<std::string> &lines = sourceLines(path);
  unsigned lo = line > SrcCtx ? line - SrcCtx : 1;
  unsigned hi = line + SrcCtx;
  if (hi > lines.size()) hi = lines.size();
  unsigned nslice = hi >= lo ? hi - lo + 1 : 0;

  std::string op = operand;
  for (char &c : op) if (c == '\n' || c == '\r') c = ' ';

  KoftaSrcmap << "@ " << kind << ' ' << nslice << ' ' << line << ' '
              << path << "\n";
  KoftaSrcmap << "= " << op << "\n";
  for (unsigned n = lo; n <= hi && n <= lines.size(); ++n)
    KoftaSrcmap << "| " << lines[n - 1] << "\n";
}

static void registerKOFTAPass(const PassManagerBuilder &, legacy::PassManagerBase &PM) {
  PM.add(new KOFTAAnalysis());
}


static RegisterStandardPasses RegisterKOFTAPass(
  PassManagerBuilder::EP_ModuleOptimizerEarly, registerKOFTAPass);
